"""Immutable answer acceptance, generic repetition feedback and formula coverage."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.document.migrations import _migrate_formula_coverage
from pdf_craft.document.render import _compose_chapter, RenderMode
from pdf_craft.extractor.chapter.chapter import Chapter, SourceAsset, DisplayFormula, encode
from pdf_craft.transformer.fill_text import text_difference
from pdf_craft.transformer.word_count import count_xml_words
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.translation_coverage import NarrativeCoverage, read_coverage, write_narrative_coverage
from pdf_craft.transformer.xml_translator.segment import search_inline_segments, search_text_segments


class FillAcceptanceBoundaryTests(TestCase):
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

    def test_end_gap_feedback_points_to_existing_field_without_changing_it(self):
        block = fromstring('<p id="9"><i>.</i></p>')
        before = tostring(block)
        error = text_difference('..', '.', [block])
        assert error is not None
        self.assertIn('END', error)
        self.assertIn('p#9/i[0].text', error)
        self.assertIn("'..'", error)
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
                _compose_chapter(result, target, set(states) if accepted else set(), set(), RenderMode.REPLACE, set())
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
