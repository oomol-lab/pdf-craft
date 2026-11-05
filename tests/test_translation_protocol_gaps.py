"""Offline regressions for blank units and invented formula transport markup."""
# pylint: disable=protected-access
import asyncio
import json
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft._policy import policy_operation
from pdf_craft.transformer.canonical_projection import MathProjectionError
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.source_math import validate_unbound_references
from pdf_craft.transformer.request_batch import RequestBatcher
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_source_math_identity import provider
from tests.translation_model import translator_fixture


class TranslationProtocolGapTests(IsolatedAsyncioTestCase):
    async def test_unicode_blank_units_preserve_inline_structure_without_requests(self):
        for blank in ('', ' ', '\u00a0', '\u2003', '\u202f', '\u3000', '\u00a0\u2002'):
            with self.subTest(blank=repr(blank)):
                translator, model = translator_fixture()
                source = fromstring(f'<body><p>{blank}<em>{blank}</em>{blank}</p><p/></body>')
                before = tostring(source)
                result, _ = await StructuredTranslation().translate(
                    translator, TranslationTask(source, SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
                self.assertEqual(tostring(result), before)
                self.assertEqual(model.calls, [])

    async def test_blank_and_prose_units_share_operation_without_losing_special_spaces(self):
        translator, model = translator_fixture()
        source = fromstring('<body><p>\u00a0</p><p>Prose\u00a0<em>with\u202fspace</em>.</p><p/></body>')
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False,
            retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(tostring(result), tostring(source))
        translation_units = [unit for batch in model.calls for unit in batch
                             if 'XML template:' not in unit[-1]['content']]
        self.assertEqual(len(translation_units), 1)

    def test_literal_protocol_documentation_is_not_removed_or_granted_new_keys(self):
        source = 'The marker <source-math key="s1"/> is documented.'
        validate_unbound_references(source, source)
        with self.assertRaises(MathProjectionError):
            validate_unbound_references(source, source + '<source-math key="s2"/>')

    @policy_operation
    async def test_direct_blank_translation_returns_original_without_requests(self):
        translator, model = translator_fixture()
        text = '\u00a0\u202f\t'
        self.assertEqual(await translator._translate_text_async(text), text)
        self.assertEqual(model.calls, [])

    async def test_invented_reference_repairs_before_fill_without_losing_prose(self):
        translator, runtime = _repairing_translator(['<xml><p id="1">Translated prose.</p></xml>'])
        calls = provider(translator, ['Translated <source-math key="s1"/> prose.', 'Translated prose.'])
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(fromstring('<body><p>Original prose.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(translation=1, fill=0))
        self.assertEqual(''.join(result.itertext()), 'Translated prose.')
        self.assertEqual(len(calls), 2)
        self.assertEqual(runtime.context_value.calls, 1)
        self.assertIn('source-math', calls[1][-1].message)
        self.assertEqual(calls[1][:2], calls[0])

    async def test_unbound_reference_variants_exhaust_translation_budget_without_fill(self):
        for response in ('Text <source-math key="s1"/>', 'Text <source-math key="s1">x</source-math>',
                         'Text <source-math key="s1">', 'Text </source-math>',
                         'Text <SOURCE-MATH key="s1"/>'):
            with self.subTest(response=response):
                translator, runtime = _repairing_translator([])
                calls = provider(translator, [response, response])
                with self.assertRaises(MathProjectionError) as caught:
                    await StructuredTranslation().translate(
                        translator, TranslationTask(fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None),
                        error_policy=False, retry_policy=RetryPolicy(translation=1, fill=12))
                self.assertEqual(len(calls), 2)
                self.assertEqual(caught.exception.attempts, 2)
                self.assertEqual(runtime.context_value.calls, 0)

    def test_blank_body_responses_remain_invalid_in_mixed_envelopes(self):
        response = json.dumps([{'id': '0', 'text': 'Valid prose.'},
                               {'id': '1', 'text': '\u00a0'}, {'id': '2', 'text': ''}])
        self.assertEqual(RequestBatcher._answers(response, 3), {'0': 'Valid prose.'})

    @policy_operation
    async def test_mixed_batch_retries_only_invalid_formula_unit(self):
        translator, model = translator_fixture()
        invocations = []

        async def invoke(messages, *_args, **_kwargs):
            invocations.append(messages)
            if len(invocations) == 1:
                units = json.loads(messages[-1].message)
                self.assertEqual(len(units), 2)
                return json.dumps([{'id': unit['id'], 'text':
                    'Valid translation.' if 'Good prose' in unit['messages'][0]['content']
                    else 'Bad <source-math key="s1"/>'} for unit in units])
            self.assertIn('Bad prose', messages[1].message)
            self.assertIn('source-math', messages[-1].message)
            return 'Repaired translation.'

        translator._translation_runtime.__dict__['_invoke'] = invoke
        answers = await asyncio.gather(*(translator._translate_text_async(text)
            for text in ('Good prose', 'Bad prose', '\u00a0')))
        self.assertEqual(answers, ['Valid translation.', 'Repaired translation.', '\u00a0'])
        self.assertEqual(len(invocations), 2)
        self.assertEqual(model.calls, [])
