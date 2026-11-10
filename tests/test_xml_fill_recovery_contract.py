"""Review regression: transparent annotations and adapter rejection share one Loop."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from pdf_craft import RetryPolicy
from pdf_craft.transformer.furniture import FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from tests.fill_fixtures import model_replies
from tests.translation_model import translator_fixture


class XMLFillRecoveryTests(IsolatedAsyncioTestCase):


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


    async def test_missing_gap_next_to_reference_passes_without_repair(self):
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
        self.assertEqual(''.join(result.itertext()), 'frame).From there.')
        self.assertEqual(len(model.wire_calls), 2)


    async def test_one_translated_sentence_can_fill_multiple_nonempty_furniture_lines(self):
        translator, model = translator_fixture()
        model_replies(model, '同一个完整句子。', [
            '<xml><section id="1">同一个完整句子。</section><section id="2"/><section id="3"/></xml>',
            '<xml><section id="1">同一个</section><section id="2">完整</section><section id="3">句子。</section></xml>'])
        sections = [FurnitureSection(1, (0, 0, 10, 10), value) for value in ['One complete', 'sentence split', 'over physical lines.']]
        target = await FurnitureXMLTransformer(translator).transform_sections(1, sections, error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(target, ['同一个', '完整', '句子。'])
        self.assertIn('LINE FRAGMENTS', model.wire_calls[-1][-1].message)
        self.assertIn('Neighboring current allocations', model.wire_calls[-1][-1].message)
        self.assertEqual(len(model.wire_calls), 3)
