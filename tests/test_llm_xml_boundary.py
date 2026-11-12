"""Model-boundary regressions; only the provider is scripted, never XML acceptance."""
# pylint: disable=protected-access
import re
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy
from pdf_craft.llm import MessageRole
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, SubmitKind
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_anchored_content_translation import _repairing_translator


def decoded(text, tag):
    return next(decode_friendly(text, tags=tag))


def repair_feedback(messages):
    return messages[-1].message.split('Validation feedback:\n', 1)[-1].split(
        '\nComplete authoritative translation', 1)[0]


def previous_xml(messages):
    match = re.search(r'Rejected candidate XML.*?```XML\n(.*?)\n```', messages[-1].message, re.S)
    assert match is not None
    return match[1]


class LLMXMLBoundaryTests(IsolatedAsyncioTestCase):

    async def test_misplaced_required_identity_is_not_told_to_be_deleted(self):
        for last in ('b', 'c'):
            with self.subTest(last=last):
                translator, runtime = _repairing_translator([
                    f'<xml><p id="1"><expression id="2">a<expression id="3">{last}</expression></expression></p></xml>',
                    '<xml><p id="1"><expression id="2">a</expression><expression id="3">b</expression></p></xml>'])
                translator._translate_text_async = AsyncMock(return_value='ab')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring(
                    '<body><p><expression id="2" display="inline">SOURCE_ONLY_LEFT</expression>'
                    '<expression id="3" display="inline">SOURCE_ONLY_RIGHT</expression></p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), 'ab')
                self.assertEqual(runtime.context_value.calls, 2)
                feedback = repair_feedback(runtime.context_value.messages[1][0])
                self.assertIn('MISPLACED, not disposable', feedback)
                self.assertNotIn('Fix: Remove all', feedback)
                self.assertIn('SOURCE_ONLY_LEFT', runtime.context_value.messages[0][0][1].message)
                self.assertIn('SOURCE_ONLY_LEFT', runtime.context_value.messages[1][0][1].message)
                self.assertNotIn('SOURCE_ONLY_LEFT', feedback)
                self.assertNotIn('SOURCE_ONLY_RIGHT', feedback)
                if last == 'b':
                    self.assertIn('ALREADY MATCHES', feedback)
                else:
                    self.assertNotIn('ALREADY MATCHES', feedback)
                    self.assertIn('Missing 1, added 1', feedback)

    async def test_repairs_keep_latest_actual_answer_and_complete_translation(self):
        values = ['BAD_ROUND_ZERO', 'BAD_ROUND_ONE', 'BAD_ROUND_TWO', 'ab']
        translator, runtime = _repairing_translator([
            f'<xml><p id="1"><em>{text}</em></p></xml>' for text in values])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p><em id="2">SOURCE_REFERENCE_ONLY</em></p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=3))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 4)
        for index, recorded in enumerate(runtime.context_value.messages):
            messages = recorded[0]
            self.assertIn('SOURCE_REFERENCE_ONLY', messages[1].message)
            self.assertEqual(messages[1].message.count('```XML'), 1 + int(index > 0))
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')
            self.assertTrue(all(message.role != MessageRole.ASSISTANT for message in messages))
            if index:
                self.assertIn(values[index-1], previous_xml(messages))
                for old in values[:index-1]:
                    self.assertNotIn(old, previous_xml(messages))
                self.assertNotIn('<translation xml:space="preserve">', repair_feedback(messages))
                self.assertIn('LOCAL correction', repair_feedback(messages))

    async def test_repairs_keep_full_source_and_actual_candidate_for_all_error_types(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><expression id="2">wrong</expression><expression id="3"/></p></xml>',
            '<xml><p id="1"><expression id="2">ab</expression></p></xml>',
            '<xml><p id="1"><expression id="2">a</expression><expression id="3">b</expression></p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p><expression id="2" display="inline">SOURCE_REFERENCE_ONLY</expression><expression id="3" display="inline">REFERENCE</expression></p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=2))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 3)
        requests = [recorded[0] for recorded in runtime.context_value.messages]
        self.assertIn('SOURCE_REFERENCE_ONLY', requests[0][1].message)
        self.assertIn('SOURCE_REFERENCE_ONLY', requests[1][1].message)
        self.assertIn('SOURCE_REFERENCE_ONLY', requests[2][1].message)
        source = re.search(r'```XML\n(.*?)\n```', requests[2][1].message, re.S)
        assert source is not None
        required = decoded(source[1], 'xml')
        self.assertEqual({node.get('id') for node in required.iter()}, {None, '1', '2', '3'})
        for messages in requests:
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')
        prior = previous_xml(requests[1])
        self.assertEqual(decoded(prior, 'xml')[0][0].text, 'wrong')

    async def test_identical_content_failure_retains_the_actual_structure_without_extra_calls(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">wrong</p></xml>', '<xml><p id="1">wrong</p></xml>',
            '<xml><p id="1">ab</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>SOURCE_REFERENCE_ONLY</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=2))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 3)
        second, third = [recorded[0] for recorded in runtime.context_value.messages[1:]]
        self.assertFalse(any(m.role == MessageRole.ASSISTANT for m in second))
        self.assertFalse(any(m.role == MessageRole.ASSISTANT for m in third))
        self.assertIn('SOURCE_REFERENCE_ONLY', third[1].message)
        previous = previous_xml(third)
        self.assertEqual(decoded(previous, 'xml')[0].get('id'), '1')
        self.assertEqual(decoded(previous, 'xml')[0].text, 'wrong')
        self.assertIn('Visible text differs', repair_feedback(third))
        for messages in (second, third):
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')

    async def test_stagnant_history_does_not_extend_budget_or_adopt_missing_content(self):
        translator, runtime = _repairing_translator(['<xml><p id="1">wrong</p></xml>'] * 13)
        artist = AsyncMock(return_value='ab')
        translator._translate_text_async = artist
        source = fromstring('<body><p>SOURCE_REFERENCE_ONLY</p></body>')
        with self.assertRaises(XMLFillError) as caught:
            await StructuredTranslation().translate(translator, TranslationTask(source, SubmitKind.REPLACE, None),
                error_policy=False, retry_policy=RetryPolicy(fill=12))
        self.assertEqual(caught.exception.attempts, 13)
        self.assertEqual(runtime.context_value.calls, 13)
        self.assertEqual(artist.await_count, 1)
        self.assertEqual(''.join(source.itertext()), 'SOURCE_REFERENCE_ONLY')

    async def test_root_level_inline_identity_is_moved_instead_of_deleted(self):
        for kind in ('matched', 'different', 'outside'):
            with self.subTest(kind=kind):
                outer = {'matched': 'ab', 'different': 'abc', 'outside': ''}[kind]
                a, b = ('a', 'b') if kind == 'outside' else ('', '')
                translator, runtime = _repairing_translator([
                    f'<xml><p id="1">{outer}</p><expression id="2">{a}</expression><expression id="3">{b}</expression></xml>',
                    '<xml><p id="1"><expression id="2">a</expression><expression id="3">b</expression></p></xml>'])
                translator._translate_text_async = AsyncMock(return_value='ab')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring(
                    '<body><p><expression id="2" display="inline">SOURCE</expression><expression id="3" display="inline">REFERENCE</expression></p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), 'ab')
                self.assertEqual(runtime.context_value.calls, 2)
                feedback = repair_feedback(runtime.context_value.messages[1][0])
                self.assertIn('MISPLACED required identity', feedback)
                self.assertIn('under xml/p#1', feedback)
                self.assertNotIn('Remove this unexpected block', feedback)
                self.assertEqual('ALREADY MATCHES' in feedback, kind == 'matched')
                self.assertEqual('Extra range in p#1.text' in feedback, kind == 'different')

    async def test_duplicate_blocks_cannot_claim_content_is_already_complete(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">wrong</p><p id="1">ab</p></xml>', '<xml><p id="1">ab</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>original</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertNotIn('RAW CONTENT STATUS', runtime.context_value.messages[1][0][-1].message)

    async def test_unowned_text_cannot_claim_content_is_already_complete(self):
        translator, runtime = _repairing_translator([
            '<xml>unowned<p id="1"><expression id="2">a<expression id="3">b</expression></expression></p></xml>',
            '<xml><p id="1"><expression id="2">a</expression><expression id="3">b</expression></p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring(
            '<body><p><expression id="2" display="inline">source</expression>'
            '<expression id="3" display="inline">text</expression></p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertNotIn('RAW CONTENT STATUS', runtime.context_value.messages[1][0][-1].message)

    async def test_fill_input_output_and_repair_history_share_codec(self):
        raw = 'A & B; <span>; &lt;span&gt;.'
        wrong = '<xml><p id="1">wrong & text</p></xml>'
        good = '<xml><p id="1">A &amp; B; &lt;span&gt;; &amp;lt;span&amp;gt;.</p></xml>'
        translator, runtime = _repairing_translator([wrong, good])
        artist = AsyncMock(return_value=raw)
        translator._translate_text_async = artist
        module = 'pdf_craft.transformer.xml_translator.xml_translator.translator'
        with patch(module + '.decode_friendly', wraps=decode_friendly) as decoder:
            result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None),
                error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(len(result[0]), 0)
        self.assertEqual(artist.await_count, 1)
        self.assertEqual(decoder.call_count, 2)
        self.assertEqual(runtime.context_value.calls, 2)
        for recorded in runtime.context_value.messages:
            messages = recorded[0]
            user = messages[1].message
            payload = re.search(r'<translation\b.*?</translation>', user, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), raw)
        retry = runtime.context_value.messages[1][0]
        previous = previous_xml(retry)
        self.assertEqual(decoded(previous, 'xml')[0].text, 'wrong & text')
        self.assertIn('Visible text differs', retry[-1].message)
        self.assertNotIn(wrong, previous)



    async def test_unparseable_xml_is_logged_but_not_reinjected_as_raw_xml(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">unfinished', '<xml><p id="1">Complete.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Complete.')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'Complete.')
        retry = runtime.context_value.messages[1][0]
        self.assertTrue(all(message.role != MessageRole.ASSISTANT for message in retry))
        self.assertIn('No complete', retry[-1].message)

    async def test_noncanonical_free_text_is_not_implicitly_xml(self):
        raw = '<arbitrary>A & B</arbitrary> &lt;literal&gt;'
        translator, runtime = _repairing_translator([
            '<xml><p id="1">&lt;arbitrary&gt;A &amp; B&lt;/arbitrary&gt; &amp;lt;literal&amp;gt;</p></xml>'])
        artist = AsyncMock(return_value=raw)
        translator._translate_text_async = artist
        result, _ = await translator.translate_element(TranslationTask(
            fromstring('<body><p>Original.</p></body>'), SubmitKind.REPLACE, None), error_policy=False)
        self.assertEqual(''.join(result.itertext()), raw)
        self.assertEqual(artist.await_count, 1)
        self.assertEqual(''.join(decoded(runtime.context_value.messages[0][0][1].message, 'translation').itertext()), raw)
