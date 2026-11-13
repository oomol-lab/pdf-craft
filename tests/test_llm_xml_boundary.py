"""Model-boundary regressions; only the provider is scripted, never XML acceptance."""
# pylint: disable=protected-access
import re
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import Element, fromstring, tostring

from tiktoken import get_encoding

from pdf_craft import RetryPolicy
from pdf_craft.llm import MessageRole
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.fill_text import text_difference, comparison_text
from pdf_craft.transformer.xml_translator.segment import (
    BlockSegment, ImmutableBlockElement, search_inline_segments, search_text_segments,
)
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing
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
    match = re.search(r'Working candidate XML.*?```XML\n(.*?)\n```', messages[-1].message, re.S)
    assert match is not None
    return match[1]


class LLMXMLBoundaryTests(IsolatedAsyncioTestCase):

    async def test_misplaced_required_identity_is_not_told_to_be_deleted(self):
        for last in ('b', 'c'):
            with self.subTest(last=last):
                translator, runtime = _repairing_translator([
                    f'<xml><p id="1"><expression signature="1">a<expression signature="2">{last}</expression></expression></p></xml>',
                    '<xml><p id="1"><expression signature="1">a</expression><expression signature="2">b</expression></p></xml>'])
                translator._translate_text_async = AsyncMock(return_value='ab')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring(
                    '<body><p><expression id="2" display="inline">SOURCE_ONLY_LEFT</expression>'
                    '<expression id="3" display="inline">SOURCE_ONLY_RIGHT</expression></p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), 'ab')
                self.assertEqual(runtime.context_value.calls, 2)
                feedback = repair_feedback(runtime.context_value.messages[1][0])
                self.assertIn('<expression signature="2">', feedback)
                self.assertNotIn('Fix: Remove all', feedback)
                self.assertIn('SOURCE_ONLY_LEFT', runtime.context_value.messages[0][0][1].message)
                self.assertNotIn('SOURCE_ONLY_LEFT', runtime.context_value.messages[1][0][1].message)
                self.assertNotIn('SOURCE_ONLY_LEFT', feedback)
                self.assertNotIn('SOURCE_ONLY_RIGHT', feedback)
                if last == 'b':
                    self.assertIn('ALREADY MATCHES', feedback)
                else:
                    self.assertNotIn('ALREADY MATCHES', feedback)
                    self.assertIn('+++ translation.normalized.txt', feedback)

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
            if index == 0:
                self.assertIn('SOURCE_REFERENCE_ONLY', messages[1].message)
            else:
                self.assertNotIn('SOURCE_REFERENCE_ONLY', messages[1].message)
            self.assertEqual(messages[1].message.count('```XML'), 1)
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')
            self.assertTrue(all(message.role != MessageRole.ASSISTANT for message in messages))
            if index:
                self.assertIn(values[index-1], previous_xml(messages))
                for old in values[:index-1]:
                    self.assertNotIn(old, previous_xml(messages))
                self.assertNotIn('<translation xml:space="preserve">', repair_feedback(messages))
                self.assertIn('+++ translation.normalized.txt', repair_feedback(messages))

    async def test_repairs_keep_full_source_contract_and_actual_candidate_for_all_error_types(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><expression signature="1">wrong</expression><expression id="3"/></p></xml>',
            '<xml><p id="1"><expression signature="1">ab</expression></p></xml>',
            '<xml><p id="1"><expression signature="1">a</expression><expression signature="2">b</expression></p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='ab')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p><expression id="2" display="inline">SOURCE_REFERENCE_ONLY</expression><expression id="3" display="inline">REFERENCE</expression></p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=2))
        self.assertEqual(''.join(result.itertext()), 'ab')
        self.assertEqual(runtime.context_value.calls, 3)
        requests = [recorded[0] for recorded in runtime.context_value.messages]
        self.assertIn('SOURCE_REFERENCE_ONLY', requests[0][1].message)
        self.assertNotIn('SOURCE_REFERENCE_ONLY', requests[1][1].message)
        self.assertNotIn('SOURCE_REFERENCE_ONLY', requests[2][1].message)
        source = re.search(r'```XML\n(.*?)\n```', requests[2][1].message, re.S)
        assert source is not None
        required = decoded(source[1], 'xml')
        self.assertEqual({node.get('signature') for node in required.iter()}, {None, '1', '2'})
        for messages in requests:
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')
        prior = previous_xml(requests[1])
        self.assertEqual(decoded(prior, 'xml')[0][0].text, 'wrong')

    async def test_repair_wire_keeps_opaque_resource_and_ids_without_restoring_editable_words(self):
        anchor_xml = '<anchor anchor_key="image&amp;one" asset_ref="image"/>'
        translator, runtime = _repairing_translator([
            '<xml>' + anchor_xml + '<p id="1">wrong<b/></p></xml>',
            '<xml>' + anchor_xml + '<p id="1">a<b>b</b></p></xml>'])
        source = fromstring('<body><p>ORIGINAL <b>REFERENCE</b></p></body>')
        before = tostring(source)
        segments = list(search_inline_segments(search_text_segments(source)))
        hill = HillClimbing(get_encoding('cl100k_base'), 10, BlockSegment('xml', segments, [
            ImmutableBlockElement(Element('anchor', anchor_key='image&one', asset_ref='image'), 0)]))
        result = await translator._request_and_submit_async(hill, 'ORIGINAL REFERENCE', 'ab',
            warp_callbacks(None, None, None),
            canonical_text_validator=lambda candidate, canonical: text_difference(
                canonical, ''.join(candidate[1].itertext())))
        self.assertIsNotNone(result)
        self.assertEqual(runtime.context_value.calls, 2)
        requests = [entry[0] for entry in runtime.context_value.messages]
        first_match = re.search(r'```XML\n(.*?)```', requests[0][1].message, re.S)
        repair_match = re.search(r'```XML\n(.*?)```', requests[1][1].message, re.S)
        assert first_match is not None and repair_match is not None
        first, repair = decoded(first_match[1], 'xml'), decoded(repair_match[1], 'xml')
        self.assertEqual(tostring(first[0]), tostring(repair[0]))
        self.assertEqual(repair[0].get('anchor_key'), 'image&one')
        self.assertEqual([(node.tag, node.get('id')) for node in first.iter()],
                         [(node.tag, node.get('id')) for node in repair.iter()])
        self.assertIn('ORIGINAL', ''.join(first[1].itertext()))
        self.assertEqual(''.join(repair[1].itertext()).strip(), 'wrong')
        self.assertEqual(decoded(previous_xml(requests[1]), 'xml')[1].text, 'wrong')
        self.assertEqual(tostring(source), before)

    async def test_empty_inline_nodes_may_precede_filled_siblings_without_losing_identity(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><i signature="2"/><i signature="3"/>'
            '<i signature="1">A</i></p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='A')
        source = fromstring('<body><p><i id="source-a">Alpha</i>'
                            '<i id="source-b">Beta</i>'
                            '<i id="source-c">Gamma</i></p></body>')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(''.join(result.itertext()), 'A')
        self.assertEqual([child.get('id') for child in result[0]], ['source-b', 'source-c', 'source-a'])
        self.assertEqual(runtime.context_value.calls, 1)

    async def test_duplicate_source_values_do_not_require_each_identity_to_be_nonempty(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><i signature="1">Echo.</i><i signature="2"/></p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Echo.')
        source = fromstring('<body><p><i id="source-a">Echo.</i>'
                            '<i id="source-b">Echo.</i></p></body>')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(''.join(result.itertext()), 'Echo.')
        self.assertEqual([child.get('id') for child in result[0]], ['source-a', 'source-b'])
        self.assertFalse(result[0][1].text)
        self.assertEqual(runtime.context_value.calls, 1)

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
        self.assertNotIn('SOURCE_REFERENCE_ONLY', third[1].message)
        previous = previous_xml(third)
        self.assertEqual(decoded(previous, 'xml')[0].get('id'), '1')
        self.assertEqual(decoded(previous, 'xml')[0].text, 'wrong')
        self.assertIn('Visible text differs', repair_feedback(third))
        for messages in (second, third):
            payload = re.search(r'<translation\b.*?</translation>', messages[1].message, re.S)
            assert payload is not None
            self.assertEqual(''.join(decoded(payload[0], 'translation').itertext()), 'ab')

    async def test_repair_uses_closest_actual_answer_with_its_own_feedback(self):
        for regression in ('<xml><p id="1">Echo.</p></xml>',
                           '<xml><p id="wrong">Echo. Echo. Echo.</p></xml>',
                           '<xml><p id="1">unterminated'):
            with self.subTest(regression=regression):
                baseline = '<xml><p id="1">Echo. Echo.</p></xml>'
                good = '<xml><p id="1">Echo. Echo. Echo.</p></xml>'
                translator, runtime = _repairing_translator([baseline, regression, good])
                translator._translate_text_async = AsyncMock(return_value='Echo. Echo. Echo.')
                source = fromstring('<body><p>Original.</p></body>')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                    source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=2))
                self.assertEqual(''.join(result.itertext()), 'Echo. Echo. Echo.')
                self.assertEqual(runtime.context_value.calls, 3)
                third = runtime.context_value.messages[2][0]
                self.assertEqual(''.join(decoded(previous_xml(third), 'xml')[0].itertext()), 'Echo. Echo.')
                self.assertIn('3 times in the translation and 2 times in the candidate', repair_feedback(third))
                self.assertIn('complete earlier answer from THIS loop', repair_feedback(third))
                self.assertIn('already passed the required source structure checks', third[1].message)
                self.assertEqual(third[1].message.count('```XML'), 1)
                self.assertIn('revising one rejected XML document', third[0].message)
                self.assertNotIn('unterminated', previous_xml(third))

    async def test_diff_locations_do_not_freeze_the_models_field_allocation(self):
        for text in ('AAB', 'A'):
            with self.subTest(text=text):
                translator, runtime = _repairing_translator([
                    f'<xml><p id="1">{text}</p><p id="2"/></xml>',
                    '<xml><p id="1"/><p id="2">AB</p></xml>'])
                translator._translate_text_async = AsyncMock(return_value='AB')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(
                    fromstring('<body><p>First source.</p><p>Second source.</p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), 'AB')
                self.assertFalse(result[0].text)
                self.assertEqual(result[1].text, 'AB')
                self.assertEqual(runtime.context_value.calls, 2)
                feedback = repair_feedback(runtime.context_value.messages[1][0])
                self.assertIn('+++ translation.normalized.txt', feedback)
                self.assertNotIn('p#1.text', feedback)
                self.assertNotIn('does not authorize emptying', feedback)

    async def test_nearer_text_with_missing_inline_node_is_a_repair_input_only(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">A<i/></p></xml>', '<xml><p id="1">AB</p></xml>',
            '<xml><p id="1"><i/>AB</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='AB')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original <i>format</i> words.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=2))
        self.assertEqual(''.join(result.itertext()), 'AB')
        self.assertEqual(runtime.context_value.calls, 3)
        third = runtime.context_value.messages[2][0]
        draft = decoded(previous_xml(third), 'xml')
        self.assertEqual(draft[0].text, 'AB')
        self.assertEqual(len(draft[0]), 0)  # No program-inserted inline node.
        self.assertEqual(third[1].message.count('```XML'), 2)
        self.assertIn('STRUCTURAL ERRORS', third[1].message)
        self.assertIn('ALREADY MATCHES', repair_feedback(third))
        self.assertNotIn('already passed the required source structure checks', third[1].message)
        self.assertEqual(len(result[0]), 1)

    async def test_zero_text_distance_never_adopts_a_structurally_incomplete_answer(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">A<i/></p></xml>', '<xml><p id="1">AB</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='AB')
        source = fromstring('<body><p>Original <i>format</i> words.</p></body>')
        original = [(node.tag, node.text, node.tail) for node in source.iter()]
        with self.assertRaises(XMLFillError) as caught:
            await StructuredTranslation().translate(translator, TranslationTask(
                source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(caught.exception.attempts, 2)
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertIn('ALREADY MATCHES', str(caught.exception))
        self.assertEqual([(node.tag, node.text, node.tail) for node in source.iter()], original)

    async def test_extra_text_feedback_does_not_protect_an_invented_wrapper(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><i>A</i><bad id="9">A</bad>B</p></xml>',
            '<xml><p id="1"><i>A</i>B</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='AB')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            fromstring('<body><p>Original <i>format</i> words.</p></body>'), SubmitKind.REPLACE, None),
            error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(''.join(result.itertext()), 'AB')
        self.assertEqual([n.tag for n in result[0]], ['i'])
        self.assertEqual(runtime.context_value.calls, 2)
        feedback = repair_feedback(runtime.context_value.messages[1][0])
        self.assertIn('no permitted source tag/signature', feedback)
        self.assertNotIn('UNWRAP', feedback)
        self.assertNotIn('keeping every element, ID and attribute', feedback)

    async def test_surplus_expression_can_be_unwrapped_without_losing_its_text(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1"><expression signature="1">a</expression> '
            '<expression signature="2">b</expression> <expression signature="999">c</expression></p></xml>',
            '<xml><p id="1"><expression signature="1">a</expression> <expression signature="2">b</expression> c</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='a b c')
        source = fromstring('<body><p><expression id="left" display="inline">u</expression>'
                            '<expression id="right" display="inline">v</expression></p></body>')
        result, _ = await StructuredTranslation().translate(translator, TranslationTask(
            source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(comparison_text(''.join(result.itertext())), 'abc')
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertEqual(len(result[0]), 2)
        self.assertEqual(result[0][-1].tail, ' c')
        self.assertIn('UNWRAP', runtime.context_value.messages[1][0][0].message)

    async def test_closest_rejected_answer_is_not_adopted_on_exhaustion(self):
        translator, runtime = _repairing_translator([
            '<xml><p id="1">Echo. Echo.</p></xml>', '<xml><p id="1">Echo.</p></xml>'])
        translator._translate_text_async = AsyncMock(return_value='Echo. Echo. Echo.')
        source = fromstring('<body><p>Original.</p></body>')
        with self.assertRaises(XMLFillError) as caught:
            await StructuredTranslation().translate(translator, TranslationTask(
                source, SubmitKind.REPLACE, None), error_policy=False, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(caught.exception.attempts, 2)
        self.assertEqual(runtime.context_value.calls, 2)
        self.assertEqual(''.join(source.itertext()), 'Original.')

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
                    f'<xml><p id="1">{outer}</p><expression signature="1">{a}</expression><expression signature="2">{b}</expression></xml>',
                    '<xml><p id="1"><expression signature="1">a</expression><expression signature="2">b</expression></p></xml>'])
                translator._translate_text_async = AsyncMock(return_value='ab')
                result, _ = await StructuredTranslation().translate(translator, TranslationTask(fromstring(
                    '<body><p><expression id="2" display="inline">SOURCE</expression><expression id="3" display="inline">REFERENCE</expression></p></body>'), SubmitKind.REPLACE, None),
                    error_policy=False, retry_policy=RetryPolicy(fill=1))
                self.assertEqual(''.join(result.itertext()), 'ab')
                self.assertEqual(runtime.context_value.calls, 2)
                feedback = repair_feedback(runtime.context_value.messages[1][0])
                self.assertIn('Invalid or missing ID attribute', feedback)
                self.assertIn('Inline contract violation', feedback)
                self.assertNotIn('Remove this unexpected block', feedback)
                self.assertNotIn('RAW CONTENT STATUS', feedback)  # Invalid root slots cannot claim a reliable content view.

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
            '<xml>unowned<p id="1"><expression signature="1">a<expression signature="2">b</expression></expression></p></xml>',
            '<xml><p id="1"><expression signature="1">a</expression><expression signature="2">b</expression></p></xml>'])
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
        self.assertIn('No parseable working XML is available', retry[-1].message)
        source_match = re.search(r'```XML\n(.*?)```', retry[-1].message, re.S)
        assert source_match is not None
        self.assertEqual(decoded(source_match[1], 'xml')[0].text, 'Original.')

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
