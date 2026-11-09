"""Formula identity stays strict while translated values may change."""
# pylint: disable=protected-access
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import Element, fromstring

from pdf_craft import SubmitKind
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.canonical_projection import SourceMath
from pdf_craft.transformer.free_translation import allocate
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.source_math import SourceMathTranslation
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_canonical_math_projection import chapter, expression, content_of
from tests.translation_model import translator_fixture


class ElasticFormulaTests(IsolatedAsyncioTestCase):
    async def test_service_364_numeric_conversion_in_pcex_and_epub(self):
        for epub in (False, True):
            with self.subTest(epub=epub):
                await self._numeric_conversion(epub)

    async def _numeric_conversion(self, epub):
        translator, model = translator_fixture()
        raw = '费用超过40亿。'
        engineer_calls = []
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                engineer_calls.append(messages)
                # The final production response's meaningful partition,
                # including omitted redundant before/after fields.
                return json.dumps([dict(id='0', end=raw, formulas=[dict(key='s1', text='40亿', parts=[
                    dict(keys=['s1'], text='40'), dict(text='亿')])])])
            owner = 'p' if epub else 'fragment'
            return f'<xml><{owner} id="1">费用超过<expression><math-ref key="m1"/></expression>。</{owner}></xml>'
        model.answer = answer
        if epub:
            source = fromstring('<body><p>Costs exceed <math id="amount"><mn>4</mn></math> billion.</p></body>')
            target, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
                translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
            formulas = target.findall('.//math')
            self.assertEqual(len(formulas), 1)
            self.assertEqual(formulas[0].get('id'), 'amount')
            self.assertEqual(formulas[0].find('semantics/annotation').text, '40')
            self.assertEqual(formulas[0].tail, '亿。')
            # The REPLACE operation mutates its input root by contract.
        else:
            source = chapter(['Costs exceed ', expression('4'), ' billion.'])
            target = await NarrativeXMLTransformer(translator).transform(source, error_policy=False)
            self.assertEqual(content_of(target), ['费用超过', expression('40'), '亿。'])
        self.assertEqual(len(engineer_calls), 1)
        self.assertEqual(len([c for c in model.wire_calls if c[0].message.startswith('You are a translator.')]), 1)


    async def test_precise_formula_span_keeps_unit_outside(self):
        translator, model = translator_fixture()
        raw = '费用超过40亿。'
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                return json.dumps([dict(id='0', end=raw, formulas=[dict(key='s1', text='40')])])
            return '<xml><fragment id="1">费用超过<expression><math-ref key="m1"/></expression>亿。</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(
            chapter(['Costs exceed ', expression('4'), ' billion.']), error_policy=False)
        self.assertEqual(content_of(target), ['费用超过', expression('40'), '亿。'])

    async def test_unchanged_flattened_formula_restores_source_notation(self):
        translator, model = translator_fixture()
        raw = '达到94%。'
        def answer(messages):
            if messages[0]['content'].startswith('You are a translator.'):
                return raw
            if messages[0]['content'].startswith('You are the structural fill engineer.'):
                return json.dumps([dict(id='0', end=raw, formulas=[dict(key='s1', text='94%')])])
            return '<xml><fragment id="1">达到<expression><math-ref key="m1"/></expression>。</fragment></xml>'
        model.answer = answer
        target = await NarrativeXMLTransformer(translator).transform(
            chapter(['Reaches ', expression(r'94\%'), '.']), error_policy=False)
        self.assertEqual(content_of(target), ['达到', expression(r'94\%'), '。'])

    def test_conversion_failure_and_internal_spacing_do_not_authorize_restoration(self):
        for source, translated in [('4', '40'), ('a b', 'ab'), ('4', '四十')]:
            math = SourceMathTranslation([SourceMath(r'\(' + source + r'\)', Element('formula'))])
            allocate(json.dumps([dict(id='0', text=translated, formulas=[dict(key='s1', text=translated)])]),
                     [SimpleNamespace(source_math=math)], translated)
            bound = math.bindings[0][0]
            assert isinstance(bound, SourceMath)
            self.assertEqual(bound.translated_latex, r'\(' + translated + r'\)')
        math = SourceMathTranslation([SourceMath(r'\(4\)', Element('formula'))])
        with patch('pdf_craft.transformer.math_binding.try_latex_to_plain_text', return_value=None):
            allocate(json.dumps([dict(id='0', text='4', formulas=[dict(key='s1', text='4')])]),
                     [SimpleNamespace(source_math=math)], '4')
        bound = math.bindings[0][0]
        assert isinstance(bound, SourceMath)
        self.assertEqual(bound.translated_latex, r'\(4\)')

    def test_changed_plain_percentage_stays_a_literal_value_not_a_tex_comment(self):
        math = SourceMathTranslation([SourceMath(r'\(94\%\)', Element('formula'))])
        allocate(json.dumps([dict(id='0', text='95%', formulas=[dict(key='s1', text='95%')])]),
                 [SimpleNamespace(source_math=math)], '95%')
        bound = math.bindings[0][0]
        assert isinstance(bound, SourceMath)
        self.assertEqual(bound.translated_latex, r'\(95\%\)')

    def test_missing_duplicate_unknown_and_ambiguous_id_bindings_still_fail(self):
        for formulas in ([], [dict(key='s2', text='40')], [dict(key='s1', text='40')]*2):
            math = SourceMathTranslation([SourceMath(r'\(4\)', Element('formula'))])
            with self.assertRaises(ValueError):
                allocate(json.dumps([dict(id='0', end='40', formulas=formulas)]), [SimpleNamespace(source_math=math)], '40')
            self.assertFalse(math.allocated)
        math = SourceMathTranslation([SourceMath(r'\(4\)', Element('formula'))])
        with self.assertRaisesRegex(ValueError, 'matches 2 occurrences'):
            allocate(json.dumps([dict(id='0', text='40 and 40', formulas=[dict(key='s1', text='40')])]),
                     [SimpleNamespace(source_math=math)], '40 and 40')
