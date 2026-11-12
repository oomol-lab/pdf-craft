"""Immutable answer acceptance, generic repetition feedback and formula coverage."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from tiktoken import get_encoding

from pdf_craft.document.migrations import _migrate_formula_coverage
from pdf_craft.document.render import _compose_chapter, RenderMode
from pdf_craft.extractor.chapter.chapter import Chapter, SourceAsset, DisplayFormula, encode
from pdf_craft.transformer.fill_text import text_difference, _literal_occurrences
from pdf_craft.transformer.xml_translator.xml import decode_friendly
from pdf_craft.transformer.word_count import count_xml_words
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.translation_coverage import NarrativeCoverage, read_coverage, write_narrative_coverage
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.segment import BlockSegment, ImmutableBlockElement
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing


class FillAcceptanceBoundaryTests(TestCase):
    def test_missing_excerpt_quotes_original_spacing_and_xml_characters(self):
        expected = 'Alice & Bob. Alice & Bob.'
        error = text_difference(expected, 'Alice & Bob.')
        assert error is not None
        excerpt = next(decode_friendly(error, tags='excerpt'))
        self.assertEqual(excerpt.text, 'Alice & Bob.')
        self.assertIn('not a new translation or a ready-made XML', error)

    def test_repetitive_math_diagnostic_quotes_data_without_building_a_candidate(self):
        passage = r"\(\frac{49x}{100} = y\) "
        expected = 'Question: ' + passage * 720 + r"\(\frac{49x}{100} ="
        actual = 'Question: ' + passage * 659 + r"\(\frac{49x}{100} ="
        candidate = Element('fragment', {'id': '1'})
        candidate.text = actual
        before = tostring(candidate)
        error = text_difference(expected, actual, [candidate])
        assert error is not None
        excerpt = next(decode_friendly(error, tags='excerpt'))
        assert excerpt.text is not None
        self.assertIn(excerpt.text, expected)
        self.assertIn(r'\frac{49x}{100} = y', excerpt.text)
        self.assertNotIn(r'\\frac', excerpt.text)
        self.assertEqual(tostring(candidate), before)
        self.assertIsNone(text_difference(expected, expected))

    def test_original_excerpt_does_not_guess_math_or_unicode_offsets(self):
        for expected in ['A e\u0301 B', r'A \(x+1\) B']:
            error = text_difference(expected, 'AB')
            assert error is not None
            self.assertNotIn('Original translation excerpt', error)

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
        source = fromstring('<body><p>Source ' + '<expression display="inline">x</expression>' * 200 + '</p></body>')
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
        candidate[0].remove(candidate[0][-1])
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

    def test_missing_repeated_text_and_punctuation_have_the_same_hint(self):
        for expected, actual in [('Echo.Echo.Echo.', 'Echo.Echo.'), ('.'*16, '.'*15),
                                 ('A\u3000B A B', 'A\tB')]:
            with self.subTest(expected=expected):
                error = text_difference(expected, actual)
                assert error is not None
                self.assertIn('NUMBER of occurrences', error)
                self.assertIn('positions', error)
        self.assertNotIn('NUMBER of occurrences', text_difference('Different', 'words') or '')

    def test_many_block_locations_do_not_hide_actionable_field_locations(self):
        blocks = [fromstring(f'<p id="{index}"><i>extra</i></p>') for index in range(20)]
        before = [tostring(block) for block in blocks]
        error = text_difference('', 'extra' * 20, blocks)
        assert error is not None
        self.assertIn('block id=0:', error)
        self.assertIn('field=p#0/i[0].text', error)
        self.assertIn('entire_field_extra=True', error)
        self.assertEqual([tostring(block) for block in blocks], before)

    def test_interior_missing_gap_reports_field_and_raw_offsets_without_editing(self):
        block = fromstring('<p id="9">a\u200bc</p>')
        before = tostring(block)
        error = text_difference('abc', 'a\u200bc', [block])
        assert error is not None
        self.assertIn('inside p#9.text', error)
        self.assertIn('VIEW offset 1', error)
        self.assertIn('raw character offset 2', error)
        self.assertEqual(tostring(block), before)

    def test_extra_range_quotes_actual_candidate_and_local_offsets_without_editing(self):
        block = fromstring('<p id="9">a\u200bbc</p>')
        before = tostring(block)
        error = text_difference('a', 'a\u200bbc', [block])
        assert error is not None
        self.assertIn('Extra range in p#9.text', error)
        self.assertIn('VIEW [1:3)', error)
        self.assertIn('raw character range [2:4)', error)
        self.assertEqual(next(decode_friendly(error, tags='extra_excerpt')).text, 'bc')
        self.assertEqual(tostring(block), before)

    def test_repeated_literal_statistics_are_generic_and_not_a_count_acceptance_gate(self):
        for unit, wanted, found in [('Echo.', 3, 2), ('.', 16, 15), ('a', 5, 4), ('Echo.', 2, 3)]:
            with self.subTest(unit=unit, wanted=wanted, found=found):
                error = text_difference(unit * wanted, unit * found)
                assert error is not None
                self.assertIn(f'Occurrences in the complete translation: {wanted}.', error)
                self.assertIn(f'Occurrences in your candidate: {found}.', error)
                self.assertIn('Counts alone do not establish correctness', error)
        self.assertEqual(_literal_occurrences('ababa', 'aba'), 2)
        self.assertEqual(_literal_occurrences('abc', ''), 0)
        self.assertIsNotNone(text_difference('Echo.A.Echo.B.', 'Echo.B.Echo.A.'))
        self.assertIsNone(text_difference('Echo.Echo.', 'Echo.Echo.'))
        self.assertIsNotNone(text_difference('Echo.' * 3, 'Repeat Echo. 3 times.'))

    def test_literal_word_diagnostics_do_not_infer_mathematical_or_sentence_units(self):
        for unit, literal in [(r'\(\frac{49x}{100}=y\)', 'frac'),
                              (r'\(\mathrm{Alter:}\)', 'Alter'),
                              (r'If the single discount of \(19\%\).', 'Ifthesinglediscountof')]:
            with self.subTest(unit=unit):
                error = text_difference(unit * 6, unit * 5)
                assert error is not None
                samples = list(decode_friendly(error, tags='literal'))
                self.assertIn(literal, [item.text for item in samples])
                self.assertIn('Occurrences in the complete translation: 6.', error)
                self.assertIn('Occurrences in your candidate: 5.', error)
                self.assertIn('NOT a formula count', error)

    def test_all_wholly_extra_fields_are_reported_by_actual_id_beyond_excerpt_limit(self):
        block = fromstring('<p id="9">Kept.' + ''.join(
            f'<i id="{100 + index}">Extra.</i>tail.' for index in range(20)) + '</p>')
        before = tostring(block)
        error = text_difference('Kept.', ''.join(block.itertext()), [block])
        assert error is not None
        for identifier in range(100, 120):
            self.assertIn(f'- p#9/i#{identifier}.text', error)
            self.assertIn(f'- p#9/i#{identifier}.tail', error)
        self.assertIn('These 40 fields consist entirely of extra', error)
        self.assertEqual(tostring(block), before)
        self.assertIsNotNone(text_difference('Kept.', ''.join(block.itertext()), [block]))

    def test_long_diff_diagnostics_are_bounded_without_shortening_the_difference(self):
        block = fromstring('<p id="9">' + 'Echo.' * 100 + '</p>')
        before = tostring(block)
        error = text_difference('Echo.' * 1000, ''.join(block.itertext()), [block])
        assert error is not None
        self.assertIn('Missing 4500, added 0 characters', error)
        self.assertIn('600 original characters', error)
        self.assertIn('Occurrences in the complete translation: 988.', error)
        self.assertLess(len(error), 6500)
        self.assertEqual(tostring(block), before)

    def test_end_gap_feedback_points_to_existing_field_without_changing_it(self):
        block = fromstring('<p id="9"><i>.</i></p>')
        before = tostring(block)
        error = text_difference('..', '.', [block])
        assert error is not None
        self.assertIn('END', error)
        self.assertIn('p#9/i[0].text', error)
        self.assertIn('NORMALIZED COMPARISON VIEW', error)
        self.assertNotIn('literal candidate', error)
        self.assertEqual(tostring(block), before)

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

    def test_math_comparison_reports_only_proven_field_offsets(self):
        block = fromstring('<p id="4">Value<expression>$20$.</expression></p>')
        before = tostring(block)
        error = text_difference('Value20.20.', 'Value$20$.', [block])
        assert error is not None
        self.assertIn('p#4/expression[0].text', error)
        self.assertIn('END', error)
        self.assertEqual(tostring(block), before)

    def test_cross_field_math_does_not_guess_normalized_field_offsets(self):
        block = fromstring(r'<p id="4">Value<expression>$20</expression>$.</p>')
        error = text_difference('Value20.20.', 'Value$20$.', [block])
        assert error is not None
        self.assertNotIn('field=', error)
        self.assertNotIn('literal candidate', error)

    def test_long_math_fallback_keeps_exact_literal_field_diagnostics(self):
        block = Element('p', id='8')
        for _ in range(700):
            SubElement(block, 'expression').text = r'\(20\)'
        actual = ''.join(block.itertext())
        error = text_difference(actual + '.', actual, [block])
        assert error is not None
        self.assertIn('p#8/expression[699].text', error)
        self.assertIn('END', error)

    def test_repeated_context_keeps_the_exact_missing_occurrence_and_location(self):
        prefix, suffix = 'Surrounding sentence. ' * 2000, ' Closing sentence.' * 2000
        block = Element('p', id='6')
        block.text = prefix + 'Echo.'
        SubElement(block, 'span').text = suffix
        error = text_difference(prefix + 'Echo.Echo.' + suffix, ''.join(block.itertext()), [block])
        assert error is not None
        self.assertIn('Missing 5, added 0', error)
        self.assertIn("='Echo.'", error)
        self.assertIn('p#6.text', error)

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
