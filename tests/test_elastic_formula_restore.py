"""Elastic values and strict identities through the single production XML fill."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import SubmitKind, RetryPolicy
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_canonical_math_projection import chapter, expression, content_of
from tests.translation_model import translator_fixture


def model_replies(model, raw, replies):
    answers = iter(replies)
    def answer(messages):
        return raw if messages[0]['content'].startswith('You are a translator.') else next(answers)
    model.answer = answer


class ElasticFormulaTests(IsolatedAsyncioTestCase):
    async def test_service_364_numeric_conversion_in_pcex_and_epub(self):
        for epub in (False, True):
            with self.subTest(epub=epub):
                translator, model = translator_fixture()
                tag = 'p' if epub else 'fragment'
                response = f'<xml><{tag} id="1">费用超过<expression><source-math key="s1">40</source-math></expression>亿。</{tag}></xml>'
                model_replies(model, '费用超过40亿。', [response])
                if epub:
                    source = fromstring('<body><p>Costs exceed <math id="amount"><mn>4</mn></math> billion.</p></body>')
                    target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
                        translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
                    node, = target.findall('.//math')
                    self.assertEqual(node.get('id'), 'amount')
                    self.assertEqual(node.find('semantics/annotation').text, '40')
                    self.assertEqual(node.tail, '亿。')
                else:
                    target = await NarrativeXMLTransformer(translator).transform(
                        chapter(['Costs exceed ', expression('4'), ' billion.']), error_policy=False)
                    self.assertEqual(content_of(target), ['费用超过', expression('40'), '亿。'])
                self.assertEqual(len(model.wire_calls), 2, 'One artist and one XML engineer, no allocation model')

    async def test_flattened_percent_and_changed_values(self):
        for old, translated, expected in [(r'94\%', '94%', r'94\%'), (r'94\%', '95%', r'95\%'),
                                           ('4', '40', '40'), ('4', '四十', '四十'), ('a b', 'ab', 'ab')]:
            with self.subTest(old=old, translated=translated):
                translator, model = translator_fixture()
                model_replies(model, f'值{translated}。', [f'<xml><fragment id="1">值<expression><source-math key="s1">{translated}</source-math></expression>。</fragment></xml>'])
                target = await NarrativeXMLTransformer(translator).transform(chapter(['Value ', expression(old), '.']), error_policy=False)
                self.assertEqual(content_of(target), ['值', expression(expected), '。'])

    async def test_failed_flattening_retains_translation_without_veto(self):
        translator, model = translator_fixture()
        model_replies(model, '值40。', ['<xml><fragment id="1">值<expression><source-math key="s1">40</source-math></expression>。</fragment></xml>'])
        with patch('pdf_craft.transformer.math_binding.try_latex_to_plain_text', return_value=None):
            result = await NarrativeXMLTransformer(translator).transform(chapter(['Value ', expression('4'), '.']), error_policy=False)
        self.assertEqual(content_of(result), ['值', expression('40'), '。'])

    async def test_missing_duplicate_unknown_and_empty_formula_ids_are_repaired_in_same_loop(self):
        valid = '<xml><fragment id="1"><expression><source-math key="s1">40</source-math></expression>亿</fragment></xml>'
        for bad in (valid.replace('<source-math key="s1">40</source-math>', '40'),
                    valid.replace('key="s1"', 'key="s9"'),
                    valid.replace('</expression>', '<source-math key="s1">40</source-math></expression>'),
                    valid.replace('>40</source-math>', '></source-math>')):
            with self.subTest(bad=bad):
                translator, model = translator_fixture()
                model_replies(model, '40亿', [bad, valid])
                target = await NarrativeXMLTransformer(translator).transform(chapter([expression('4'), ' billion']), error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(content_of(target), [expression('40'), '亿'])
                self.assertEqual(len(model.wire_calls), 3)
                self.assertIn('40亿', model.wire_calls[-1][1].message)

    async def test_real_text_edits_exhaust_without_partial_adoption(self):
        translator, model = translator_fixture()
        bad = '<xml><fragment id="1"><expression><source-math key="s1">4</source-math></expression>亿</fragment></xml>'
        model_replies(model, '40亿', [bad, bad])
        source = chapter([expression('4'), ' billion'])
        with self.assertRaises(XMLFillError):
            await NarrativeXMLTransformer(translator).transform(source, error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(content_of(source), [expression('4'), ' billion'])
        self.assertEqual(len(model.wire_calls), 3)
