"""Source values and occurrence identities survive translation, fill and decoding."""
# pylint: disable=protected-access,unused-argument
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from types import MethodType
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.extractor.chapter.chapter import InlineExpression
from pdf_craft.transformer.canonical_projection import MathProjectionError
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, XMLTranslator
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_canonical_math_projection import chapter, expression, content_of


def provider(translator, replies):
    """Exercise the real first-stage protocol and budget, without network calls."""
    responses = iter(replies)
    calls = []

    async def invoke(messages, *_args, **_kwargs):
        calls.append(messages)
        return next(responses)

    translator._translate_text_async = MethodType(XMLTranslator._translate_text_async, translator)
    translator._translation_runtime.__dict__['_invoke'] = invoke
    return calls


class SourceMathIdentityTests(IsolatedAsyncioTestCase):
    async def test_changed_value_repairs_in_translation_before_any_fill(self):
        fill = '<xml><fragment id="1">值 <expression><math-ref key="m1"/></expression>。</fragment></xml>'
        translator, runtime = _repairing_translator([fill])
        calls = provider(translator, ['$x=0$', '值 <source-math key="s1"/>。'])
        source = chapter(['Value ', expression('x=1'), '.'])
        saved = deepcopy(source)
        target = await NarrativeXMLTransformer(translator).transform(
            source, error_policy=False, retry_policy=RetryPolicy(translation=1, fill=0))
        self.assertEqual(len(calls), 2)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual(content_of(target), ['值 ', expression('x=1'), '。'])
        self.assertEqual(source, saved)
        self.assertIn('source counterpart', calls[1][-1].message)
        self.assertIn(r'\(x=1\)', calls[0][-1].message)

    async def test_changed_values_exhaust_bounded_budget_without_fill(self):
        with TemporaryDirectory() as directory:
            translator, runtime = _repairing_translator([])
            translator._translation_llm.failure_log_path = Path(directory)
            calls = provider(translator, ['$x=0$', '$x=0$'])
            with self.assertRaises(MathProjectionError) as caught:
                await NarrativeXMLTransformer(translator).transform(
                    chapter([expression('x=1')]), error_policy=False,
                    retry_policy=RetryPolicy(translation=1, fill=12))
            self.assertEqual(len(calls), 2)
            self.assertEqual(runtime.context_value.calls, 0)
            self.assertEqual(caught.exception.attempts, 2)
            self.assertTrue((Path(directory) / getattr(caught.exception, 'failure_log_id') / 'summary.json').exists())

    async def test_reordered_values_ignore_the_old_fill_slot_identity(self):
        translator, _ = _repairing_translator([
            '<xml><fragment id="1">否则 <expression id="2"><math-ref key="m1"/></expression>；'
            '条件成立时 <expression id="3"><math-ref key="m2"/></expression>。</fragment></xml>'])
        provider(translator, ['否则 <source-math key="s2"/>；条件成立时 <source-math key="s1"/>。'])
        target = await NarrativeXMLTransformer(translator).transform(
            chapter([expression('x=1'), ' or ', expression('x=0')]), error_policy=False)
        self.assertEqual(content_of(target), ['否则 ', expression('x=0'), '；条件成立时 ', expression('x=1'), '。'])

    async def test_unequal_formula_counts_require_repair_not_new_math(self):
        translator, runtime = _repairing_translator([
            '<xml><fragment id="1">模 <expression id="2"><math-ref key="m1"/></expression>；'
            '成立时 <expression id="3"><math-ref key="m2"/></expression>，否则 '
            '<expression id="4"><math-ref key="m3"/></expression>。</fragment></xml>'])
        calls = provider(translator, [r'$\chi$ 是模 $q$ 的特征；成立时 $x=1$，否则 $x=0$。',
            '模 <source-math key="s1"/>；成立时 <source-math key="s2"/>，否则 <source-math key="s3"/>。'])
        target = await NarrativeXMLTransformer(translator).transform(
            chapter([expression(r'\chi\mod q'), ', ', expression('x=1'), ' or ', expression('x=0')]),
            error_policy=False, retry_policy=RetryPolicy(translation=1))
        self.assertEqual(len(calls), 2)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual([x.content for x in content_of(target) if isinstance(x, InlineExpression)],
                         [r'\chi\mod q', 'x=1', 'x=0'])

    async def test_equal_math_with_distinct_epub_ids_reorders_original_nodes(self):
        source = fromstring('<body><p>First <math id="first"><mi>x</mi></math> then '
                            '<math id="second"><mi>x</mi></math>.</p></body>')
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Second <expression id="2"><math-ref key="m1"/></expression> then '
            'first <expression id="3"><math-ref key="m2"/></expression>.</p></xml>'])
        # Literal equality cannot identify which node/anchor moved. Retry with
        # source identities, then check actual original MathML IDs after submit.
        calls = provider(translator, ['$x$ then $x$.',
            'Second <source-math key="s2"/> then first <source-math key="s1"/>.'])
        result, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False,
            retry_policy=RetryPolicy(translation=1))
        self.assertEqual(len(calls), 2)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertEqual([node.get('id') for node in result.findall('.//math')], ['second', 'first'])

    async def test_epub_math_conversion_failure_still_restores_original_mathml(self):
        source = fromstring('<body><p>Value <math id="original"><mi>x</mi></math>.</p></body>')
        translator, _ = _repairing_translator([
            '<xml><p id="1">值 <expression><math-ref key="m1"/></expression>。</p></xml>'])
        provider(translator, ['值 <source-math key="s1"/>。'])
        with patch('pdf_craft.pipeline.epub.translation.xml_interrupter.process_mathml', side_effect=ValueError):
            result, _ = await StructuredTranslation(formulas=XMLInterrupter()).translate(
                translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(result.find('.//math').get('id'), 'original')
        self.assertEqual(result.find('.//mi').text, 'x')

    async def test_missing_duplicate_unknown_and_invented_references_never_reach_fill(self):
        for response in ('<source-math key="s2"/>', '<source-math key="s1"/><source-math key="s1"/>',
                         'no formula', '<source-math key="s1"/> plus $x=0$',
                         '<source-math key="s1"/> <SOURCE-MATH key="s2"/>',
                         '<source-math key="s1">$x=0$</source-math>',
                         '<source-math key="s1">$x=1$'):
            with self.subTest(response=response):
                translator, runtime = _repairing_translator([])
                provider(translator, [response])
                with self.assertRaises(MathProjectionError):
                    await NarrativeXMLTransformer(translator).transform(
                        chapter([expression('x=1')]), error_policy=False, retry_policy=RetryPolicy(translation=0))
                self.assertEqual(runtime.context_value.calls, 0)

    async def test_empty_paired_reference_is_recovered_without_a_retry(self):
        translator, _ = _repairing_translator([
            '<xml><fragment id="1"><expression><math-ref key="m1"/></expression></fragment></xml>'])
        calls = provider(translator, ['<source-math key="s1"> </source-math>'])
        target = await NarrativeXMLTransformer(translator).transform(chapter([expression('x=1')]), error_policy=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(content_of(target), [expression('x=1')])

    async def test_repair_feedback_identifies_missing_and_duplicated_occurrences(self):
        translator, _ = _repairing_translator([
            '<xml><fragment id="1"><expression id="2"><math-ref key="m1"/></expression> '
            '<expression id="3"><math-ref key="m2"/></expression></fragment></xml>'])
        calls = provider(translator, ['<source-math key="s2"/> <source-math key="s2"/>',
                                     '<source-math key="s1"/> <source-math key="s2"/>'])
        target = await NarrativeXMLTransformer(translator).transform(
            chapter([expression('x=1'), ' ', expression('x=0')]), error_policy=False,
            retry_policy=RetryPolicy(translation=1))
        self.assertEqual([value.content for value in content_of(target) if isinstance(value, InlineExpression)],
                         ['x=1', 'x=0'])
        self.assertIn("Missing keys: ['s1']", calls[1][-1].message)
        self.assertIn("'s2': 2", calls[1][-1].message)

    async def test_readable_reference_payload_must_remain_the_original_formula(self):
        translator, _ = _repairing_translator([
            '<xml><fragment id="1"><expression><math-ref key="m1"/></expression></fragment></xml>'])
        calls = provider(translator, [r'<source-math key="s1">\(x=0\)</source-math>',
                                      r'<source-math key="s1">\(x=1\)</source-math>'])
        target = await NarrativeXMLTransformer(translator).transform(
            chapter([expression('x=1')]), error_policy=False, retry_policy=RetryPolicy(translation=1))
        self.assertEqual(len(calls), 2)
        self.assertEqual(content_of(target), [expression('x=1')])
        self.assertIn('payload changed', calls[1][-1].message)

    async def test_literal_unchanged_math_is_recovered_without_extra_request(self):
        translator, _ = _repairing_translator([
            '<xml><fragment id="1"><expression><math-ref key="m1"/></expression></fragment></xml>'])
        calls = provider(translator, ['$x=1$'])
        target = await NarrativeXMLTransformer(translator).transform(chapter([expression('x=1')]), error_policy=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(content_of(target), [expression('x=1')])

    async def test_unlabelled_reordered_math_may_not_mutate_values(self):
        translator, runtime = _repairing_translator([])
        translator._translate_text_async = AsyncMock(return_value='$x=0$ then $x=0$')
        with self.assertRaises(MathProjectionError):
            await NarrativeXMLTransformer(translator).transform(
                chapter([expression('x=1'), ' then ', expression('x=0')]), error_policy=False)
        self.assertEqual(runtime.context_value.calls, 0)
