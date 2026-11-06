"""Translation transport integrity, separate from content grouping regressions."""
# pylint: disable=protected-access
import asyncio
import json
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy
from pdf_craft._policy import policy_operation
from pdf_craft.concurrency import ConcurrentExecutor, FixedCapacity
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, InlineExpression, SourceAsset, Reference, search_references_in_chapter
from pdf_craft.expression import ExpressionKind
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.llm import Message, MessageRole
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.request_batch import RequestBatcher, MAX_BATCH_UNITS, request_window
from pdf_craft.transformer.group_requests import GroupRequests
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, SubmitKind
from pdf_craft.usage import UsageCollector, UsageScope, shared_request_usage, usage_context, request_attempt


from tests.translation_model import translator_fixture, paragraph

class RequestBatchTests(IsolatedAsyncioTestCase):
    @policy_operation
    async def test_unit_and_token_limits_split_ready_calls_without_executor_deadlock(self):
        translator, model = translator_fixture()
        # A single provider slot must not be occupied while waiting for peers.
        translator._translation_runtime.executor = ConcurrentExecutor(FixedCapacity(1))
        async def request(index):
            text = f'unit {index} ' + ('long text ' * (1500 if index % 5 == 0 else 1))
            async with translator._translation_runtime.context() as context:
                result = await translator._requests.request(context, [Message(MessageRole.USER, text)],
                    runtime=translator._translation_runtime, stage='fill')
                self.assertEqual(result, text)
        await asyncio.wait_for(asyncio.gather(*(request(index) for index in range(25))), 3)
        self.assertLess(len(model.calls), 25)
        self.assertTrue(all(len(batch) <= MAX_BATCH_UNITS for batch in model.calls))
        self.assertEqual(model.peak, 1)
        self.assertEqual(translator._requests.pending, {})

    @policy_operation
    async def test_window_limits_requests_without_limiting_content_readiness(self):
        for window in (1, 2):
            translator, model = translator_fixture()
            translator._requests.budget = 4096  # Force several fill batches.
            original = model.invoke
            active = peak = 0
            async def invoke(*args, invoke_original=original, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.01)
                    return await invoke_original(*args, **kwargs)
                finally:
                    active -= 1
            translator._translation_runtime.__dict__['_invoke'] = invoke
            translator._fill_runtime.__dict__['_invoke'] = invoke
            async def roots():
                for index in range(24):
                    yield TranslationTask(fromstring(f'<html><body><p>Paragraph {index}.</p></body></html>'),
                                          SubmitKind.REPLACE, index)
            results = await asyncio.wait_for(translator._translate_elements(
                roots(), **StructuredTranslation().arguments(), window=window), 5)
            self.assertEqual(len(results), 24)
            self.assertGreater(len(model.calls), 2)
            self.assertEqual(peak, window)
            self.assertEqual(active, 0)

    @policy_operation
    async def test_compact_batches_enforce_unit_and_wire_token_limits(self):
        translator, model = translator_fixture()
        shared = 'Shared translation instruction. ' * 500
        async def request(index):
            async with translator._translation_runtime.context() as context:
                return await translator._requests.request(context, [
                    Message(MessageRole.SYSTEM, shared), Message(MessageRole.USER, f'unit {index}')],
                    runtime=translator._translation_runtime, stage='fill')
        results = await asyncio.wait_for(asyncio.gather(*(request(index) for index in range(145))), 5)
        self.assertEqual(results, [f'unit {index}' for index in range(145)])
        self.assertTrue(any(len(batch) == MAX_BATCH_UNITS for batch in model.calls))
        self.assertTrue(all(len(batch) <= MAX_BATCH_UNITS for batch in model.calls))
        encoding = await translator._translation_runtime.encoding()
        for messages in model.wire_calls:
            self.assertEqual(messages[0].message.count(shared), 1)
            self.assertNotIn(shared, messages[1].message)
            units = json.loads(messages[1].message)
            response = json.dumps([{'id': unit['id'], 'text': unit['messages'][0]['content']} for unit in units])
            wire_tokens = sum(len(encoding.encode(message.message)) for message in messages)
            self.assertLessEqual(wire_tokens + len(encoding.encode(response)), translator._requests.budget)

    async def test_repeated_local_formula_ids_stay_in_their_own_units(self):
        translator, model = translator_fixture()
        units = [AnchoredContent(str(index), 0, -1, SourceAsset(
            1, 'image', (0, 0, 100, 100), caption=[f'Caption {index} ',
                InlineExpression(ExpressionKind.INLINE_DOLLAR, letter), ' after.']), '')
                 for index, letter in enumerate(('x', 'y'))]
        results = await AnchoredContentXMLTransformer(translator).transform_assets(units)
        self.assertEqual([result.caption for result in results if result is not None],
                         [unit.asset.caption for unit in units])
        self.assertEqual(len(model.calls), 3)

    async def test_exhausted_unit_retains_real_batch_evidence_and_counts_usage_once(self):
        with TemporaryDirectory() as directory:
            translator, model = translator_fixture()
            config = translator._fill_llm
            config.failure_log_path = Path(directory)
            original = model.answer
            def answer(messages):
                value = original(messages)
                if 'XML template:' in messages[1]['content']:
                    return value.replace('Paragraph 1.', 'wrong')
                return value
            model.answer = answer
            collector = UsageCollector()
            await NarrativeXMLTransformer(translator).transform_many([
                (Chapter(index, 1, [paragraph(index)]), index) for index in range(2)],
                error_policy=True, retry_policy=RetryPolicy(fill=1), usage=collector)
            logs = list(Path(directory).iterdir())
            self.assertEqual(len(logs), 1)
            summary = json.loads((logs[0] / 'summary.json').read_text())
            self.assertEqual(summary['attempts'], 2)
            transports = [json.loads(line) for line in (logs[0] / 'transports.jsonl').read_text().splitlines()]
            requests = [record for record in transports if record['event'] == 'request']
            self.assertEqual(len(requests), 2)
            self.assertIn('Execute each independent conversation', requests[0]['messages'][0]['content'])
            self.assertNotIn('Paragraph 0.', requests[1]['messages'][1]['content'])
            usage = collector.report()[config]
            self.assertEqual(usage.effective.requests, 2)
            self.assertEqual(usage.wasted.requests, 1)

    async def test_independent_operations_never_share_a_batch(self):
        translator, model = translator_fixture()
        async def operation(index):
            return await NarrativeXMLTransformer(translator).transform_many([
                (Chapter(index, 1, [paragraph(index)]), index)],
                request_headers={'X-Document': str(index)})
        await asyncio.gather(operation(0), operation(1))
        self.assertEqual(len(model.calls), 4)
        self.assertTrue(all(len(batch) == 1 for batch in model.calls))

    @policy_operation
    async def test_partial_fill_envelope_is_not_committed_to_cache(self):
        with TemporaryDirectory() as directory:
            translator, _ = translator_fixture()
            translator._translation_llm.cache_path = Path(directory)
            calls = 0
            async def partial(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return '[{"id":"0","text":"accepted"}]'
            translator._translation_runtime.__dict__['_invoke'] = partial
            async def request(index):
                async with translator._translation_runtime.context() as context:
                    return await translator._requests.request(context,
                        [Message(MessageRole.USER, f'source {index}')],
                        runtime=translator._translation_runtime, stage='fill')
            for _ in range(2):
                results = await asyncio.gather(request(0), request(1), return_exceptions=True)
                self.assertEqual(results[0], 'accepted')
                self.assertEqual(results[1], '')
                self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(calls, 2)

    async def test_body_and_footnote_with_reused_ids_keep_chapter_ownership(self):
        translator, model = translator_fixture()
        chapters = []
        for index in range(2):
            note = Reference(1, 1, '*', [TextFlowItem('body', 0, [
                SourceTextFragment(1, 2, (0, 50, 100, 70), [f'Note {index}.'])])])
            body = TextFlowItem('body', 0, [SourceTextFragment(
                1, 1, (0, 0, 100, 30), [f'Body {index}.', note])])
            chapters.append((Chapter(index, 1, [body]), index))
        results = await NarrativeXMLTransformer(translator).transform_many(chapters)
        for index, chapter in enumerate(results):
            notes = list(search_references_in_chapter(chapter))
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].mark, '*')
            flow = notes[0].flow_items[0]
            assert isinstance(flow, TextFlowItem)
            fragment = flow.children[0]
            assert isinstance(fragment, SourceTextFragment)
            self.assertEqual(fragment.content, [f'Note {index}.'])
        self.assertEqual(len(model.calls), 2)

    async def test_identity_validation_rejects_duplicate_unknown_and_preserves_missing(self):
        self.assertEqual(RequestBatcher._answers('[{"id":"0","text":"a"},{"id":"0","text":"b"}]', 2), {})
        self.assertEqual(RequestBatcher._answers('[{"id":"9","text":"a"}]', 2), {})
        self.assertEqual(RequestBatcher._answers('[{"id":"1","text":"b"}]', 2), {'1': 'b'})
        self.assertEqual(RequestBatcher._answers('[{"id":"0","id":"1","text":"b"}]', 2), {})
        self.assertEqual(RequestBatcher._answers(r'[{"id":"0","text":"a\u0000b\ud800c\u200b"}]', 1), {'0': 'abc\u200b'})
        self.assertEqual(RequestBatcher._answers('```json\n[{"id":"0","text":"a"}]\n```', 1), {'0': 'a'})

    @policy_operation
    async def test_cancel_one_waiter_keeps_sibling_and_last_cancel_joins_transport(self):
        translator, _ = translator_fixture()
        batcher = translator._requests
        entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def blocked(messages, *_args, **_kwargs):
            # Encoding/template IO can reverse arrival order. IDs belong
            # to actual envelope members, never task creation order.
            if messages[0].role == MessageRole.SYSTEM:
                units = json.loads(messages[-1].message)
                self.assertEqual(len(units), 2)
                answer = json.dumps([dict(id=unit['id'], text={
                    'first': 'a', 'second': 'b'}[unit['messages'][-1]['content']]) for unit in units])
            else:
                answer = 'last'
            entered.set()
            try:
                await release.wait()
                return answer
            finally:
                closed.set()
        translator._translation_runtime.__dict__['_invoke'] = blocked
        async def request(text, group):
            with group.member():
                async with translator._translation_runtime.context() as context:
                    return await batcher.request(context, [Message(MessageRole.USER, text)],
                                                 runtime=translator._translation_runtime, stage='fill')
        group = GroupRequests(2)
        first, second = asyncio.create_task(request('first', group)), asyncio.create_task(request('second', group))
        await entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(closed.is_set())
        release.set()
        self.assertEqual(await second, 'b')
        self.assertTrue(closed.is_set())
        entered.clear()
        release.clear()
        closed.clear()
        last = asyncio.create_task(request('last', GroupRequests(1)))
        await entered.wait()
        last.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await last
        self.assertTrue(closed.is_set())
        self.assertEqual(batcher.pending, {})
        self.assertEqual(batcher.timers, {})

    @policy_operation
    async def test_cancel_queued_transport_releases_ownership_without_sending(self):
        translator, model = translator_fixture()
        batcher = translator._requests
        batcher.budget = 1  # Keep the two conversations in separate requests.
        entered, queued, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_invoke = model.invoke
        original_execute = batcher._execute
        executions = 0
        async def invoke(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_invoke(*args, **kwargs)
        async def execute(*args):
            nonlocal executions
            executions += 1
            if executions == 2:
                queued.set()
            return await original_execute(*args)
        translator._translation_runtime.__dict__['_invoke'] = invoke
        async def request(text):
            async with translator._translation_runtime.context() as context:
                return await batcher.request(context, [Message(MessageRole.USER, text)],
                                             runtime=translator._translation_runtime, stage='fill')
        with request_window(1), patch.object(batcher, '_execute', execute):
            first = asyncio.create_task(request('first'))
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                second = asyncio.create_task(request('second'))
                await asyncio.wait_for(queued.wait(), 2)
                second.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await second
                release.set()
                self.assertEqual(await asyncio.wait_for(first, 2), 'first')
                self.assertEqual(await asyncio.wait_for(request('third'), 2), 'third')
            finally:
                release.set()
                for task in (first, second):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(task for task in (first, second) if task is not None),
                                     return_exceptions=True)
        self.assertEqual(len(model.calls), 2)
        self.assertEqual(batcher.pending, {})
        self.assertEqual(batcher.timers, {})

    def test_shared_usage_is_effective_when_only_nonleader_is_adopted(self):
        collector, provider = UsageCollector(), object()
        with usage_context(collector):
            # Scopes must remain live while their shared transport runs.
            first = UsageScope()
            second = UsageScope()
            with shared_request_usage([first, second]):
                with request_attempt(provider) as attempt:
                    assert attempt is not None
                    attempt.accept()
            first.settle(False)
            second.settle(True)
        report = collector.report()[provider]
        self.assertEqual(report.effective.requests, 1)
        self.assertEqual(report.wasted.requests, 0)
