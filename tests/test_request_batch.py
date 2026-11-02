"""Physical-request regression tests: no HTTP, OCR or model calls."""
# pylint: disable=protected-access
import asyncio
import json
import re
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring

from pdf_craft import AsyncPDFCraft, RetryPolicy
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import encode
from tests.test_pdf_craft_extraction import make_extraction
from pdf_craft._policy import policy_operation
from pdf_craft.concurrency import ConcurrentExecutor, FixedCapacity
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, InlineExpression, SourceAsset, Reference, search_references_in_chapter
from pdf_craft.expression import ExpressionKind
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.llm import LLM, Message, MessageRole
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.request_batch import RequestBatcher, MAX_BATCH_UNITS, request_window
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator, TranslationTask, SubmitKind
from pdf_craft.usage import UsageCollector, UsageScope, shared_request_usage, usage_context, request_attempt


class EchoModel:
    """Exercise real runtime, cache and fill validation, replacing only HTTP."""
    def __init__(self):
        self.calls = []
        self.wire_calls = []
        self.active = self.peak = 0

    async def invoke(self, messages, *_args, **_kwargs):
        self.wire_calls.append(messages)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if messages[0].message.startswith('Execute each independent conversation'):
                units = json.loads(messages[-1].message)
                for unit in units:
                    if unit['messages'][0]['role'] != 'system':
                        unit['messages'].insert(0, {'role': 'system', 'content': messages[0].message})
                self.calls.append([unit['messages'] for unit in units])
                return json.dumps([{'id': unit['id'], 'text': self.answer(unit['messages'])}
                                   for unit in reversed(units)])
            units = [[{'role': message.role.name.lower(), 'content': message.message} for message in messages]]
            self.calls.append(units)
            return self.answer(units[0])
        finally:
            self.active -= 1

    @staticmethod
    def answer(messages):
        user = messages[1 if len(messages) > 1 else 0]['content']
        template = re.search(r'XML template:\n```XML\n(.*?)\n```', user, re.S)
        return template[1] if template else user


class ReadyBatcher(RequestBatcher):
    """Flush the fixed experiment only after all six inputs are actually ready.

    The real 10ms deadline is tested separately. Source IO speed must not
    decide the expected physical request count in a packing experiment.
    """
    def __init__(self, units):
        super().__init__(10000)
        self.units = units

    def _launch(self, key):
        batch = self.pending.get(key)
        if batch is None:
            return
        if len(batch.calls) >= self.units:
            super()._launch(key)
        else:
            self.timers[key] = asyncio.get_running_loop().call_later(0.001, self._launch, key)


def translator_fixture(*, ready_units=None, max_group_score=10000):
    model = EchoModel()
    config = LLM('test', 'https://example.invalid', 'offline', 'cl100k_base', retry_times=0)
    translator = XMLTranslator(config, config, 'English', None, 10000, max_group_score,
                               executor=ConcurrentExecutor(FixedCapacity(2)))
    if ready_units is not None:
        translator._requests = ReadyBatcher(ready_units)
    translator._translation_runtime.__dict__['_invoke'] = model.invoke
    translator._fill_runtime.__dict__['_invoke'] = model.invoke
    return translator, model


@contextmanager
def simultaneous_calls(translator, count):
    """Control input readiness, leaving production packing/deadlines unchanged.

    A default-window regression deadlocks here if admission still waits for
    the preceding semantic unit to complete before reading its successor.
    """
    request = translator._requests.request
    arrivals = {}
    gates = {}
    async def ready(context, messages, *, stage, **options):
        if options.get('retry_index', 0) == 0:
            arrivals[stage] = arrivals.get(stage, 0) + 1
            gate = gates.setdefault(stage, asyncio.Event())
            if arrivals[stage] == count:
                gate.set()
            await gate.wait()
        return await request(context, messages, stage=stage, **options)
    with patch.object(translator._requests, 'request', ready):
        yield


def paragraph(index):
    return TextFlowItem('body', 0, [SourceTextFragment(index, index, (0, 0, 100, 30), [f'Paragraph {index}.'])])


class RequestBatchTests(IsolatedAsyncioTestCase):
    async def test_pcex_layout_does_not_multiply_physical_requests(self):
        reports = []
        for fragmented in (False, True):
            translator, model = translator_fixture(ready_units=6)
            chapters = ([Chapter(i, 1, [paragraph(i)]) for i in range(6)] if fragmented else
                        [Chapter(0, 1, [paragraph(i) for i in range(6)])])
            results = await asyncio.wait_for(NarrativeXMLTransformer(translator, window=2).transform_many(
                [(chapter, str(index)) for index, chapter in enumerate(chapters)]), 30)
            actual = [fragment.content for chapter in results for flow in chapter.flow_items if isinstance(flow, TextFlowItem)
                      for fragment in flow.children if isinstance(fragment, SourceTextFragment)]
            self.assertEqual(actual, [[f'Paragraph {i}.'] for i in range(6)])
            counts = {'translation': 0, 'fill': 0}
            for batch in model.calls:
                counts['fill' if 'XML template:' in batch[0][1]['content'] else 'translation'] += 1
            self.assertEqual(counts, {'translation': 1, 'fill': 1})
            self.assertLessEqual(model.peak, 2)
            reports.append(counts)
        print('TRANSLATION_BATCH_COUNTS', json.dumps({
            'scope': 'offline HTTP substitute; controlled input readiness; real runtime and canonical fill',
            'one_chapter': reports[0], 'six_chapters': reports[1], 'paragraphs': 6}))
        self.assertEqual(reports[0], reports[1])

    @policy_operation
    async def test_async_epub_roots_keep_own_payload_and_node_mapping(self):
        translator, model = translator_fixture(max_group_score=2600)
        async def roots():
            for index in range(6):
                await asyncio.sleep(0.02)  # Deliberately slower than the production flush deadline.
                yield TranslationTask(fromstring(f'<html><body><p>Paragraph {index}.</p></body></html>'),
                                      SubmitKind.REPLACE, index)
        delivered = []
        protocol = StructuredTranslation()
        with simultaneous_calls(translator, 6):
            results = await asyncio.wait_for(translator._translate_elements(roots(), **protocol.arguments(),
                                                        on_result=lambda value: delivered.append(value[1])), 5)
        self.assertEqual(delivered, list(range(6)))
        self.assertEqual([(index, root.findtext('body/p')) for root, index in results],
                         [(index, f'Paragraph {index}.') for index in range(6)])
        self.assertEqual(len(model.calls), 2)

    async def test_real_pcex_schedule_batches_small_chapters_and_persists_coverage(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', page_pixel_sizes={i: (100, 100) for i in range(1, 7)})
            for path in (root / 'source/chapters').glob('*.xml'):
                path.unlink()
            for index in range(6):
                save_xml(encode(Chapter(index + 1, 1, [paragraph(index + 1)])),
                         root / f'source/chapters/chapter_{index + 1}.xml')
            translator, model = translator_fixture(max_group_score=2600)
            model.answer = lambda messages: EchoModel.answer(messages).replace("Paragraph", "Translated")
            with simultaneous_calls(translator, 6):
                result = await asyncio.wait_for(AsyncPDFCraft().translate_extraction(
                    source, root / 'translated.pcex', translator,
                    target_language='English', with_anchored=False), 5)
            with result._materialize() as paths:
                assert paths.translations is not None
                index = json.loads((paths.translations / 'index.json').read_text())
                self.assertTrue(index)
                coverage_files = list(paths.translations.glob('*/coverage.xml'))
                self.assertEqual(len(coverage_files), 1)
                entries = fromstring(coverage_files[0].read_text()).findall('./narrative/paragraph')
                self.assertEqual(len(entries), 6)
                self.assertTrue(all(entry.get('state') == 'translated' for entry in entries))
                text = ''.join(path.read_text() for path in paths.translations.glob('*/chapters/*.xml'))
                for number in range(1, 7):
                    self.assertIn(f'Translated {number}.', text)
                self.assertNotIn('Paragraph', text)
            self.assertEqual(len(model.calls), 2)
            print('PCEX_SCHEDULE_BATCH_COUNTS', json.dumps({
                'physical_requests': len(model.calls), 'paragraphs': 6, 'scope': 'offline transport; controlled input readiness',
                'translation': sum('XML template:' not in batch[0][1]['content'] for batch in model.calls),
                'fill': sum('XML template:' in batch[0][1]['content'] for batch in model.calls)}))

    @policy_operation
    async def test_dense_epub_and_fragmented_roots_share_instructions_and_requests(self):
        for fragmented in (False, True):
            translator, model = translator_fixture()
            model.answer = lambda messages: EchoModel.answer(messages).replace('Paragraph', 'Translated')
            async def roots(fragmented=fragmented):
                for index, indexes in enumerate(([i] for i in range(48)) if fragmented else [range(48)]):
                    yield TranslationTask(fromstring('<html><body>' + ''.join(
                        f'<p>Paragraph {i}.</p>' for i in indexes) + '</body></html>'), SubmitKind.REPLACE, index)
            with simultaneous_calls(translator, 48):
                results = await asyncio.wait_for(translator._translate_elements(
                    roots(), **StructuredTranslation().arguments()), 5)
            self.assertEqual([p.text for root, _ in results for p in root.findall('body/p')],
                             [f'Translated {i}.' for i in range(48)])
            self.assertEqual(len(model.calls), 2)  # Original dense EPUB also used 1 + 1.
            self.assertEqual([len(batch) for batch in model.calls], [48, 48])
            for messages in model.wire_calls:
                self.assertEqual(len(messages), 2)
                units = json.loads(messages[1].message)
                self.assertTrue(all(all(m['role'] != 'system' for m in u['messages']) for u in units))
            fill = model.wire_calls[1]
            self.assertIn('Canonical visible-text constraint', fill[0].message)
            self.assertNotIn('Canonical visible-text constraint', fill[1].message)

    @policy_operation
    async def test_accepted_paragraph_delivers_before_later_paragraph_finishes_repair(self):
        translator, model = translator_fixture()
        original = model.invoke
        repairing, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        fills = 0
        async def invoke(messages, *args, **kwargs):
            nonlocal fills
            filling = any('XML template:' in message.message for message in messages)
            if filling:
                fills += 1
                if fills == 2:
                    repairing.set()
                    await release.wait()
            response = await original(messages, *args, **kwargs)
            if filling and fills == 1:
                # Accept the first paragraph; only the later paragraph repairs.
                items = json.loads(response)
                for item in items:
                    item['text'] = item['text'].replace('Paragraph 1.', 'wrong')
                response = json.dumps(items)
            return response
        translator._translation_runtime.__dict__['_invoke'] = invoke
        translator._fill_runtime.__dict__['_invoke'] = invoke
        async def roots():
            yield TranslationTask(fromstring(
                '<html><body><p>Paragraph 0.</p><p>Paragraph 1.</p></body></html>'), SubmitKind.REPLACE, 0)
        values = []
        def notify(mapping):
            values.append(''.join(text.text for text in mapping[1]))
            delivered.set()
        with simultaneous_calls(translator, 2):
            task = asyncio.create_task(translator._translate_elements(
                roots(), **StructuredTranslation().arguments(), on_mapping=notify))
            try:
                await asyncio.wait_for(repairing.wait(), 5)
                await asyncio.wait_for(delivered.wait(), 2)
                self.assertEqual(values, ['Paragraph 0.'])
                self.assertFalse(task.done())
            finally:
                release.set()
                await asyncio.wait_for(task, 5)
        self.assertEqual(values, ['Paragraph 0.', 'Paragraph 1.'])

    async def test_partial_fill_retries_only_rejected_unit(self):
        translator, model = translator_fixture()
        original = model.answer
        rejected = False
        def answer(messages):
            nonlocal rejected
            value = original(messages)
            if 'XML template:' in messages[1]['content'] and 'Paragraph 1.' in value and not rejected:
                rejected = True
                return value.replace('Paragraph 1.', 'wrong')
            return value
        model.answer = answer
        results = await NarrativeXMLTransformer(translator).transform_many([
            (Chapter(index, 1, [paragraph(index)]), index) for index in range(2)])
        self.assertEqual(len(results), 2)
        fills = [batch for batch in model.calls if 'XML template:' in batch[0][1]['content']]
        self.assertEqual([len(batch) for batch in fills], [2, 1])
        self.assertIn('Paragraph 1.', fills[1][0][1]['content'])
        self.assertNotIn('Paragraph 0.', fills[1][0][1]['content'])

    @policy_operation
    async def test_short_batch_delivers_while_input_is_waiting(self):
        translator, _ = translator_fixture()
        delivered, release = asyncio.Event(), asyncio.Event()
        async def roots():
            yield TranslationTask(fromstring('<html><body><p>First.</p></body></html>'),
                                  SubmitKind.REPLACE, 0)
            await release.wait()
            yield TranslationTask(fromstring('<html><body><p>Last.</p></body></html>'),
                                  SubmitKind.REPLACE, 1)
        protocol = StructuredTranslation()
        task = asyncio.create_task(translator._translate_elements(
            roots(), **protocol.arguments(), window=2,
            on_mapping=lambda _value: delivered.set()))
        try:
            await asyncio.wait_for(delivered.wait(), 2)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            await task

    @policy_operation
    async def test_unit_and_token_limits_split_ready_calls_without_executor_deadlock(self):
        translator, model = translator_fixture()
        # A single provider slot must not be occupied while waiting for peers.
        translator._translation_runtime.executor = ConcurrentExecutor(FixedCapacity(1))
        async def request(index):
            text = f'unit {index} ' + ('long text ' * (1500 if index % 5 == 0 else 1))
            async with translator._translation_runtime.context() as context:
                result = await translator._requests.request(context, [Message(MessageRole.USER, text)],
                    runtime=translator._translation_runtime, stage='translation')
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
            async def invoke(*args, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.01)
                    return await original(*args, **kwargs)
                finally:
                    active -= 1
            translator._translation_runtime.__dict__['_invoke'] = invoke
            translator._fill_runtime.__dict__['_invoke'] = invoke
            async def roots():
                for index in range(24):
                    yield TranslationTask(fromstring(f'<html><body><p>Paragraph {index}.</p></body></html>'),
                                          SubmitKind.REPLACE, index)
            with simultaneous_calls(translator, 24):
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
                    runtime=translator._translation_runtime, stage='translation')
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
        self.assertEqual(len(model.calls), 2)

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
    async def test_partial_translation_envelope_is_not_committed_to_cache(self):
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
                        runtime=translator._translation_runtime, stage='translation')
            for _ in range(2):
                results = await asyncio.gather(request(0), request(1), return_exceptions=True)
                self.assertEqual(results[0], 'accepted')
                self.assertIsInstance(results[1], ValueError)
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
        async def blocked(*_args, **_kwargs):
            entered.set()
            try:
                await release.wait()
                return '[{"id":"0","text":"a"},{"id":"1","text":"b"}]'
            finally:
                closed.set()
        translator._translation_runtime.__dict__['_invoke'] = blocked
        async def request():
            async with translator._translation_runtime.context() as context:
                return await batcher.request(context, [Message(MessageRole.USER, 'source')],
                                             runtime=translator._translation_runtime, stage='translation')
        first, second = asyncio.create_task(request()), asyncio.create_task(request())
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
        last = asyncio.create_task(request())
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
                                             runtime=translator._translation_runtime, stage='translation')
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
