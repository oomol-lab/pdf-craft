"""Review regression: transparent annotations and adapter rejection share one Loop."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from pdf_craft import RetryPolicy
from pdf_craft.transformer.furniture import FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_canonical_math_projection import chapter, expression, content_of
from tests.test_elastic_formula_restore import model_replies
from tests.translation_model import translator_fixture


class XMLFillRecoveryTests(IsolatedAsyncioTestCase):
    async def test_annotation_wrapping_required_expression_keeps_structure_and_identity(self):
        translator, model = translator_fixture()
        model_replies(model, r'值 \(x\)。', [r'<xml><fragment id="1">值 <source-math key="s1"><expression>\(x\)</expression></source-math>。</fragment></xml>'])
        result = await NarrativeXMLTransformer(translator).transform(chapter(['Value ', expression('x'), '.']), error_policy=False)
        self.assertEqual(content_of(result), ['值 ', expression('x'), '。'])
        self.assertEqual(len(model.wire_calls), 2)

    async def test_annotation_wrapping_invalid_format_cannot_bypass_structure_check(self):
        translator, model = translator_fixture()
        bad = r'<xml><fragment id="1"><source-math key="s1"><invented>\(x\)</invented></source-math></fragment></xml>'
        model_replies(model, r'\(x\)', [bad])
        with self.assertRaises(XMLFillError):
            await NarrativeXMLTransformer(translator).transform(chapter([expression('x')]), error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(len(model.wire_calls), 2)

    async def test_missing_merged_identity_is_located_and_repaired_without_retranslation(self):
        translator, model = translator_fixture()
        bad = '<xml><fragment id="1"><expression id="2"><source-math key="s1"><math-ref key="m1"/></source-math></expression><expression id="3"/></fragment></xml>'
        good = r'<xml><fragment id="1"><expression id="2">\(<source-math key="s1">x=</source-math><source-math key="s2">y</source-math>\)</expression><expression id="3"/></fragment></xml>'
        model_replies(model, r'\(x=y\)', [bad, good])
        result = await NarrativeXMLTransformer(translator).transform(chapter([expression('x='), expression('y')]), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(content_of(result), [expression('x='), expression('y')])
        feedback = model.wire_calls[-1][-1].message
        self.assertIn('s2: source value', feedback)
        self.assertIn('source block id=1', feedback)
        self.assertIn('merged', feedback)
        self.assertEqual(len(model.wire_calls), 3)

    async def test_nonempty_furniture_sections_retry_before_adoption(self):
        translator, model = translator_fixture()
        model_replies(model, '左。\n\n右。', [
            '<xml><section id="1">左。右。</section><section id="2"/></xml>',
            '<xml><section id="1">左。</section><section id="2">右。</section></xml>'])
        sections = [FurnitureSection(1, (0, 0, 10, 10), 'Left.'), FurnitureSection(1, (20, 0, 30, 10), 'Right.')]
        result = await FurnitureXMLTransformer(translator).transform_sections(1, sections, error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(result, ['左。', '右。'])
        self.assertEqual(len(model.wire_calls), 3)
        self.assertIn('Empty XML block ids', model.wire_calls[-1][-1].message)

    async def test_repeated_transport_value_is_safe_only_with_full_text_and_distinct_source_ids(self):
        for raw, replies in [('$x$ $x$', 1), ('$x$ $y$', 2)]:
            with self.subTest(raw=raw):
                translator, model = translator_fixture()
                response = '<xml><fragment id="1"><expression id="2"><source-math key="s1"><math-ref key="m1"/></source-math></expression> <expression id="3"><source-math key="s2"><math-ref key="m1"/></source-math></expression></fragment></xml>'
                corrected = response.replace('<source-math key="s2"><math-ref key="m1"/>', '<source-math key="s2"><math-ref key="m2"/>')
                model_replies(model, raw, [response, corrected])
                source = chapter([expression('x'), ' ', expression('x' if replies == 1 else 'y')])
                target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(content_of(target), content_of(source))
                self.assertEqual(len(model.wire_calls), 1+replies)
                if replies == 2:
                    self.assertIn('Missing', model.wire_calls[-1][-1].message)

    async def test_missing_gap_next_to_reference_gets_exact_field_feedback(self):
        from xml.etree.ElementTree import fromstring
        from pdf_craft import SubmitKind
        from pdf_craft.transformer.structured_translation import StructuredTranslation
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        translator, model = translator_fixture()
        bad = '<xml><fragment id="1">frame).<ref/>From there.</fragment></xml>'
        good = bad.replace('<ref/>From', '<ref/> From')
        model_replies(model, 'frame). From there.', [bad, good])
        source = fromstring('<text><fragment>cadre).<ref display="inline"/>Depuis ici.</fragment></text>')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'frame). From there.')
        self.assertEqual(len(model.wire_calls), 3)
        feedback = model.wire_calls[-1][-1].message
        self.assertIn("expected gap ' '", feedback)
        self.assertIn('ref id=none', feedback)
        self.assertIn(".tail='From there.'", feedback)
