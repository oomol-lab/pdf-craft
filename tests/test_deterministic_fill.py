"""Canonical text with a unique allocation does not need model copying."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy, SubmitKind
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_anchored_content_translation import _repairing_translator
from tests.test_source_math_identity import provider


class DeterministicFillTests(IsolatedAsyncioTestCase):

    async def test_long_translation_into_one_plain_slot_needs_no_fill_request(self):
        translator, runtime = _repairing_translator([], direct_fill=True)
        canonical = 'Translated text & literal <span> markup.\u00a0' * 500
        calls = provider(translator, [canonical])
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(fromstring('<body><p>Original prose.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(''.join(result.itertext()), canonical)
        self.assertEqual(len(calls), 1)
        self.assertEqual(runtime.context_value.calls, 0)


    async def test_changed_inline_allocation_still_uses_model_and_exact_validation(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Translated <em>important</em> prose.</p></xml>'], direct_fill=True)
        provider(translator, ['Translated important prose.'])
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(fromstring('<body><p>Original <em>important</em> prose.</p></body>'),
                                        SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(''.join(result.itertext()), 'Translated important prose.')
        self.assertEqual(result.find('.//em').text, 'important')
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_ambiguous_inline_fill_still_rejects_changed_words_and_duplicate_ids(self):
        for response in (
                '<xml><p id="1">Changed <em>important</em> prose.</p></xml>',
                '<xml><p id="1">Translated <em>important</em> prose.</p>'
                '<p id="1">Translated <em>important</em> prose.</p></xml>'):
            with self.subTest(response=response):
                translator, runtime = _repairing_translator([response, response], direct_fill=True)
                provider(translator, ['Translated important prose.'])
                with self.assertRaises(XMLFillError):
                    await StructuredTranslation().translate(
                        translator, TranslationTask(fromstring('<body><p>Original <em>important</em> prose.</p></body>'),
                                                    SubmitKind.REPLACE, None),
                        error_policy=False, retry_policy=RetryPolicy(translation=0, fill=1))
                self.assertEqual(runtime.context_value.calls, 2)


    async def test_multiple_changed_blocks_do_not_guess_allocation(self):
        # Probe candidate generation directly: the ordinary mapper may split
        # separate paragraphs into independently allocatable single-slot units.
        from tiktoken import get_encoding
        from pdf_craft.transformer.xml_translator.segment import BlockSegment, search_inline_segments, search_text_segments
        from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing
        from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLTranslator
        root = fromstring('<body><p>First original.</p><p>Second original.</p></body>')
        segments = list(search_inline_segments(search_text_segments(root)))
        hill = HillClimbing(get_encoding('cl100k_base'), 100, BlockSegment('xml', segments))
        candidates = list(XMLTranslator._direct_fill_candidates(hill, 'Different translation.'))
        self.assertEqual(len(candidates), 0)

    async def test_source_only_joiner_slot_is_empty_but_retains_its_tag(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><span>Translated name</span><span>\u2060</span> follows.</p></xml>'], direct_fill=True)
        provider(translator, ['Translated name follows.'])
        result, _ = await StructuredTranslation().translate(
            translator, TranslationTask(fromstring(
                '<body><p><span>Original name</span><span>\u2060</span> follows.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(''.join(result.itertext()), 'Translated name\u2060 follows.')
        self.assertEqual(len(result.findall('.//span')), 2)
        self.assertEqual(result.findall('.//span')[1].text, '\u2060')
        self.assertEqual(runtime.context_value.calls, 1)


    async def test_nontext_unit_validator_does_not_adopt_untranslated_source(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Translated field.</p></xml>'], direct_fill=True)
        provider(translator, ['Translated field.'])
        source = fromstring('<body><p>Original field.</p></body>')
        result, _ = await StructuredTranslation(owner=lambda _segment: source,
                                                is_text_unit=lambda _owner: False).translate(
            translator, TranslationTask(source, SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(translation=0, fill=0))
        self.assertEqual(''.join(result.itertext()), 'Translated field.')
        self.assertEqual(runtime.context_value.calls, 1)
