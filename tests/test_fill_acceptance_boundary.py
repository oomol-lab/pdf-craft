"""Immutable answer acceptance, generic repetition feedback and formula coverage."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import Element, fromstring, tostring
from tiktoken import get_encoding

from pdf_craft.document.migrations import _migrate_formula_coverage
from pdf_craft.document.render import _compose_chapter, RenderMode
from pdf_craft.extractor.chapter.chapter import Chapter, SourceAsset, DisplayFormula, encode
from pdf_craft.transformer.fill_text import text_difference, comparison_text
from tests.fill_diff_helpers import apply_displayed_diff
from pdf_craft.transformer.word_count import count_xml_words
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.translation_coverage import NarrativeCoverage, read_coverage, write_narrative_coverage
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.segment import BlockSegment, ImmutableBlockElement
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing


class FillAcceptanceBoundaryTests(TestCase):
    def test_missing_inline_id_keeps_the_error_fact_without_an_edit_instruction(self):
        from pdf_craft.transformer.xml_translator.segment import InlineLostIDError
        from pdf_craft.transformer.xml_translator.xml_translator.validation import _format_inline_error
        parent = fromstring('<p><i>value</i></p>')
        message = _format_inline_error(get_encoding('cl100k_base'),
                                      InlineLostIDError(parent[0], [parent]), 1)
        self.assertIn('p#1 > i', message)
        self.assertIn('missing an ID attribute', message)
        self.assertNotIn('Fix:', message)

    def test_diff_preserves_literal_xml_characters_in_comparison_data(self):
        expected, actual = 'Alice & Bob. Alice & Bob.', 'Alice & Bob.'
        error = text_difference(expected, actual)
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, actual), comparison_text(expected))
        self.assertIn('+Alice&Bob.', error)

    def test_repetitive_math_diagnostic_does_not_build_or_modify_a_candidate(self):
        passage = r"\(\frac{49x}{100} = y\) "
        expected, actual = passage*12, passage*10
        candidate = Element('fragment', {'id': '1'})
        candidate.text = actual
        before = tostring(candidate)
        error = text_difference(expected, actual, [candidate])
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, actual), comparison_text(expected))
        self.assertEqual(tostring(candidate), before)

    def test_unicode_and_math_diffs_use_the_complete_comparison_view(self):
        for expected in ['A e\u0301 B', r'A \(x+1\) B']:
            error = text_difference(expected, 'AB')
            assert error is not None
            self.assertEqual(apply_displayed_diff(error, 'AB'), comparison_text(expected))

    def test_empty_structure_keeps_resource_identity_and_original_reference(self):
        source = fromstring('<body><p>SOURCE_ONLY <b>REFERENCE</b></p></body>')
        segments = list(search_inline_segments(search_text_segments(source)))
        anchor = Element('anchor', anchor_key='image&one', asset_ref='image')
        hill = HillClimbing(get_encoding('cl100k_base'), 10, BlockSegment(
            'xml', segments, [ImmutableBlockElement(anchor, 0)]))
        full = hill.request_element()
        before = tostring(source)
        template = hill.request_template()
        self.assertEqual(''.join(template.itertext()).strip(), '')
        self.assertEqual(tostring(template[0]), tostring(full[0]))
        self.assertEqual(template[0].get('anchor_key'), 'image&one')
        self.assertEqual(tostring(source), before)
        self.assertIn('SOURCE_ONLY', ''.join(hill.request_element().itertext()))

    def test_large_structure_feedback_preserves_all_nodes_without_repeating_trees(self):
        source = fromstring('<body><p>Source ' + '<expression display="inline">x</expression>' * 400 + '</p></body>')
        segments = list(search_inline_segments(search_text_segments(source)))
        hill = HillClimbing(get_encoding('cl100k_base'), 10, BlockSegment('xml', segments))
        original = tostring(hill.request_element())
        template = hill.request_template()
        from pdf_craft.transformer.xml_translator.xml import encode_friendly
        encoded = encode_friendly(template, preserve_whitespace=True)
        self.assertGreater(encoded.count('\n'), 199)
        self.assertEqual([node.get('id') for node in template.iter('expression')],
                         [node.get('id') for node in hill.request_element().iter('expression')])
        candidate = deepcopy(hill.request_element())
        candidate[0][:] = []
        self.assertIsNotNone(hill.validate(candidate))
        self.assertEqual(hill.inline_feedback_blocks(candidate), [])
        self.assertEqual(tostring(hill.request_element()), original)

    def test_expression_defaults_are_not_repeated_but_real_source_attributes_survive(self):
        source = fromstring('<body><p>Value <expression display="inline" class="source-math">x</expression>.</p></body>')
        segments = list(search_inline_segments(search_text_segments(source)))
        hill = HillClimbing(get_encoding('cl100k_base'), 10, BlockSegment('xml', segments))
        candidate = hill.request_element()
        expression = next(candidate.iter('expression'))
        self.assertNotIn('display', expression.attrib)
        self.assertNotIn('white-space', expression.attrib)
        self.assertIsNone(hill.validate(candidate))
        mappings = hill.candidate_mappings(candidate)
        assert mappings[0] is not None
        from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit, SubmitKind
        submit(source, SubmitKind.REPLACE, [mapping for mapping in mappings if mapping is not None])
        actual = next(source.iter('expression'))
        self.assertEqual(actual.get('display'), 'inline')
        self.assertEqual(actual.get('class'), 'source-math')
        self.assertEqual(actual.text, 'x')

    def test_retired_formula_context_name_does_not_exempt_text_from_counting(self):
        self.assertEqual(count_xml_words(fromstring('<body><formula_context>Visible words.</formula_context></body>')), 2)

    def test_missing_repeated_text_and_punctuation_have_conditional_count_hints(self):
        for expected, actual in [('Echo.Echo.Echo.', 'Echo.Echo.'), ('.'*16, '.'*15), ('A　B A B', 'A\tB')]:
            with self.subTest(expected=expected):
                error = text_difference(expected, actual)
                assert error is not None
                self.assertIn('Repeated occurrences may account', error)
                self.assertEqual(apply_displayed_diff(error, actual), comparison_text(expected))
        self.assertNotIn('Repeated occurrences', text_difference('Different', 'words') or '')

    def test_feedback_does_not_select_xml_field_destinations(self):
        blocks = [fromstring(f'<p id="{index}"><i>extra</i></p>') for index in range(20)]
        before = [tostring(block) for block in blocks]
        error = text_difference('', 'extra'*20, blocks)
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, 'extra'*20), '')
        self.assertNotIn('p#', error)
        self.assertEqual([tostring(block) for block in blocks], before)

    def test_missing_period_and_extra_wrapper_are_independent_repairs(self):
        source = fromstring('<body><p><b><i>.</i></b></p><p>Next</p></body>')
        segments = list(search_inline_segments(search_text_segments(source)))
        hill = HillClimbing(get_encoding('cl100k_base'), 10, BlockSegment('xml', segments))
        candidates = [
            ('<xml><p id="1"><b><i>.</i></b></p><p id="2">Next</p></xml>', True, False),
            ('<xml><p id="1"><b><i>.</i></b></p><p id="2"><b><i>.</i></b>Next</p></xml>', True, True),
            ('<xml><p id="1"><b><i>.</i></b></p><p id="2">.Next</p></xml>', True, True),
            ('<xml><p id="1"><b><i>..</i></b></p><p id="2">Next</p></xml>', True, True),
        ]
        for xml, structure_valid, content_valid in candidates:
            with self.subTest(xml=xml):
                candidate = fromstring(xml)
                before = tostring(candidate)
                self.assertEqual(hill.validate(candidate) is None, structure_valid)
                self.assertEqual(text_difference('..Next', ''.join(candidate.itertext())) is None,
                                 content_valid)
                self.assertEqual(tostring(candidate), before)





    def test_repetition_diagnostics_never_replace_complete_text_acceptance(self):
        for unit, wanted, found in [('Echo.', 3, 2), ('.', 16, 15), ('a', 5, 4), ('Echo.', 2, 3),
                                   (r'\(\frac{49x}{100}=y\)', 6, 5)]:
            with self.subTest(unit=unit, wanted=wanted, found=found):
                self.assertIsNotNone(text_difference(unit * wanted, unit * found))
                self.assertIsNone(text_difference(unit * wanted, unit * wanted))
        self.assertIsNotNone(text_difference('Echo.A.Echo.B.', 'Echo.B.Echo.A.'))
        self.assertIsNotNone(text_difference('Echo.' * 3, 'Repeat Echo. 3 times.'))



    def test_moderate_diff_payload_is_complete(self):
        expected, actual = 'Repeated phrase. '*100, 'Repeated phrase. '*20
        error = text_difference(expected, actual)
        assert error is not None
        self.assertNotIn('Display truncated', error)
        self.assertEqual(apply_displayed_diff(error, actual), comparison_text(expected))

    def test_long_diff_diagnostics_are_bounded_but_acceptance_remains_exact(self):
        actual, expected = 'Echo.'*100, 'Echo.'*3000
        error = text_difference(expected, actual)
        assert error is not None
        self.assertIn('Display truncated', error)
        self.assertIn('6000 characters per changed range', error)
        self.assertLess(len(error), 14000)
        self.assertIsNone(text_difference(expected, expected))
        self.assertIsNotNone(text_difference(expected, actual))


    def test_acceptance_is_read_only_even_when_a_tail_can_be_delivered(self):
        source = fromstring('<body><fragment>source</fragment><fragment>source</fragment></body>')
        segments = list(search_inline_segments(search_text_segments(source)))
        for index, segment in enumerate(segments, 1):
            segment.id = index
        candidate = fromstring('<xml><fragment id="1">a</fragment>b<fragment id="2">c</fragment></xml>')
        before = tostring(candidate)
        protocol = StructuredTranslation(owner=lambda _segment: source)
        self.assertIsNone(protocol.validate(segments, 'abc', candidate))
        self.assertEqual(tostring(candidate), before)
        self.assertEqual(candidate[0].text, 'a')
        self.assertEqual(candidate[0].tail, 'b')

    def test_diff_coordinates_belong_to_comparison_text_not_original_xml(self):
        block = fromstring('<p id="4">Value<expression>$20$.</expression></p>')
        before = tostring(block)
        error = text_difference('Value20.20.', 'Value$20$.', [block])
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, 'Value$20$.'), comparison_text('Value20.20.'))
        self.assertNotIn('p#4', error)
        self.assertEqual(tostring(block), before)

    def test_cross_field_math_diff_stays_in_the_global_comparison_view(self):
        block = fromstring(r'<p id="4">Value<expression>$20</expression>$.</p>')
        error = text_difference('Value20.20.', 'Value$20$.', [block])
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, 'Value$20$.'), comparison_text('Value20.20.'))
        self.assertNotIn('field=', error)


    def test_long_repeated_context_keeps_a_complete_precise_diff(self):
        prefix, suffix = 'Surrounding sentence. '*2000, ' Closing sentence.'*2000
        actual, expected = prefix+'Echo.'+suffix, prefix+'Echo.Echo.'+suffix
        error = text_difference(expected, actual)
        assert error is not None
        self.assertEqual(apply_displayed_diff(error, actual), comparison_text(expected))
        self.assertLess(len(error), 2000)

    def test_formula_coverage_round_trips_and_controls_rendering(self):
        source = encode(Chapter(5, -1, [DisplayFormula(SourceAsset(1, 'formula', (0,0,10,10), content=['x+1']))]))
        target = deepcopy(source)
        content = target.find('.//content')
        assert content is not None
        content.text = 'x+2'
        with TemporaryDirectory() as directory:
            path = Path(directory)/'coverage.xml'
            write_narrative_coverage(path, [NarrativeCoverage('5', 'formula', 'body:0', 'translated')])
            states = read_coverage(path).narrative
            self.assertEqual(states, {('5', 'formula', 'body:0'): 'translated'})
            for accepted in (False, True):
                result = deepcopy(source)
                _compose_chapter(result, target, set(states) if accepted else set(), set(), RenderMode.REPLACE)
                self.assertEqual(result.findtext('.//content'), 'x+2' if accepted else 'x+1')

    def test_old_layers_mark_frozen_formulas_preserved_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            chapters = root/'chapters';chapters.mkdir()
            layers = root/'translations';layer = layers/'old';layer.mkdir(parents=True)
            (chapters/'chapter_5.xml').write_text('<chapter id="5"><flow><display-formula><asset ref="formula"><content>x</content></asset></display-formula></flow><references><ref id="2-1"><flow><display-formula><asset ref="formula"><content>y</content></asset></display-formula></flow></ref></references></chapter>')
            for path in [root/'translation.xml', layer/'coverage.xml']:
                path.write_text('<translation><narrative/></translation>')
            _migrate_formula_coverage(chapters, layers, root/'translation.xml')
            for path in [root/'translation.xml', layer/'coverage.xml']:
                self.assertEqual(read_coverage(path).narrative, {
                    ('5','formula','body:0'): 'preserved', ('5','formula','note:2-1:0'): 'preserved'})
