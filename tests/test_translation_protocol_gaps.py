"""Offline regressions for blank units and invented formula transport markup."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft._policy import policy_operation
from pdf_craft.transformer.structured_translation import StructuredTranslation
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
                expected = fromstring('<body><p/><p/></body>')
                expected[0].text = normalize_whitespace(blank) or None
                from xml.etree.ElementTree import SubElement
                child = SubElement(expected[0], 'em')
                child.text = child.tail = normalize_whitespace(blank) or None
                result, _ = await StructuredTranslation().translate(
                    translator, TranslationTask(source, SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
                self.assertEqual(tostring(result), tostring(expected))
                self.assertEqual(model.calls, [])

    async def test_blank_and_prose_units_share_ordinary_whitespace_policy(self):
        translator, model = translator_fixture()
        source = fromstring('<body><p>\u00a0</p><p>Prose\u00a0<em>with\u202fspace</em>.</p><p/></body>')
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None), error_policy=False,
            retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(tostring(result), tostring(source))
        translation_units = [unit for batch in model.calls for unit in batch
                             if 'XML template:' not in unit[-1]['content']]
        self.assertEqual(len(translation_units), 1)


    @policy_operation
    async def test_direct_blank_translation_returns_original_without_requests(self):
        translator, model = translator_fixture()
        text = '\u00a0\u202f\t'
        self.assertEqual(await translator._translate_text_async(text), text)
        self.assertEqual(model.calls, [])





    @policy_operation
    async def test_arbitrary_and_empty_artist_responses_go_unchanged_to_fill(self):
        for raw in ('', 'Text <source-math key="s1"/>', 'Text <SOURCE-MATH key="other">', '  free text  '):
            with self.subTest(raw=raw):
                translator, _runtime = _repairing_translator([])
                calls = provider(translator, [raw])
                self.assertEqual(await translator._translate_text_async('Original prose.'), raw)
                self.assertEqual(len(calls), 1)
