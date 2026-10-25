"""Deterministic terminal-cause races; no live providers or model resources."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from pdf_craft import ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity, JEV
from pdf_craft.concurrency import (
    NonContinuableError, fatal_error_boundary, task_group_error,
)
from pdf_craft.error import OCRBillingError
from pdf_craft.jev import JEVRuntime
from pdf_craft.pdf import vendor_ocr
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime
from pdf_craft.transformer.xml_translator.xml_translator.concurrency import run_async_parameters


def exception_graph(error):
    """Walk all exception links as production callers do, rejecting cycles."""
    visited, active, statuses = set(), set(), []

    def visit(node):
        if not isinstance(node, BaseException):
            return
        if id(node) in active:
            raise AssertionError(f"exception graph cycle at {type(node).__name__}")
        if id(node) in visited:
            return
        visited.add(id(node))
        active.add(id(node))
        if isinstance(node, httpx.HTTPStatusError):
            statuses.append(node.response.status_code)
        for child in (node.__cause__, node.__context__, *node.args,
                      *getattr(node, 'exceptions', ())):
            visit(child)
        active.remove(id(node))

    visit(error)
    return statuses


def fatal_error(status=402):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://ocr.invalid'))
    raw = httpx.HTTPStatusError('provider rejected request', request=response.request, response=response)
    return NonContinuableError('terminal', cause=raw)


class _HeldFailureCapacity(FixedCapacity):
    """Let cancelled siblings finish before the originating task can finish."""
    def __init__(self):
        super().__init__(2)
        self.failure_releasing = asyncio.Event()
        self.allow_release = asyncio.Event()
        self.reports = []

    async def release(self, report):
        if isinstance(report.error, NonContinuableError):
            self.failure_releasing.set()
            await self.allow_release.wait()
        self.reports.append(report)
        await super().release(report)


class FatalErrorPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_http_status_survives_cancelled_sibling_collected_first(self):
        for status in (402, 429):
            for fatal_page in (1, 2):
                with self.subTest(status=status, fatal_page=fatal_page):
                    async with asyncio.timeout(5):
                        await self._ocr_http_case(status, fatal_page)

    async def _ocr_http_case(self, status, fatal_page):
        both_started, sibling_closed, collected = (asyncio.Event() for _ in range(3))
        started = set()
        original = []
        capacity = _HeldFailureCapacity()
        executor = ConcurrentExecutor(capacity)
        checked = vendor_ocr._checked_response

        async def handler(request) -> httpx.Response:
            content = json.loads(request.content)['messages'][0]['content']
            image = next(item['image_url']['url'] for item in content if item['type'] == 'image_url')
            page = int(image.rsplit('/', 1)[1])
            started.add(page)
            if len(started) == 2:
                both_started.set()
            try:
                await both_started.wait()
                if page == fatal_page:
                    return httpx.Response(status, request=request, json={
                        'error': {'type': 'insufficient_quota', 'message': 'balance exhausted'},
                    })
                await asyncio.Event().wait()
                raise AssertionError('cancelled request continued')
            finally:
                if page != fatal_page:
                    sibling_closed.set()

        def capture(*args):
            try:
                return checked(*args)
            except OCRBillingError as error:
                original.append(error)
                raise

        def collect(tasks):
            error = task_group_error(tasks)
            if original and error is original[0] and capacity.failure_releasing.is_set():
                collected.set()
            return error

        async def resolver(path):
            return f'https://images.invalid/{path.name}'

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        config = DeepSeekOCRVendorConfig(
            base_url='https://ocr.invalid', api_key='test', model='test', retry_times=0,
        )
        with patch.object(vendor_ocr.httpx, 'AsyncClient', return_value=client), \
                patch.object(vendor_ocr, '_checked_response', side_effect=capture), \
                patch.object(vendor_ocr, 'task_group_error', side_effect=collect):
            async with VendorOCRRuntime(config, executor, resolver) as runtime:
                async def consume():
                    return [result async for result in runtime.request_many(
                        VendorOCRInput(page, Path(str(page)), lambda: False) for page in (1, 2)
                    )]
                consumer = asyncio.create_task(consume())
                try:
                    await collected.wait()
                    await sibling_closed.wait()
                    self.assertTrue(sibling_closed.is_set())
                    self.assertFalse(consumer.done())  # Origin still releasing its lease.
                finally:
                    capacity.allow_release.set()
                with self.assertRaises(OCRBillingError) as raised:
                    await consumer
                self.assertIs(raised.exception, original[0])
                self.assertEqual(exception_graph(raised.exception), [status])
                self.assertEqual(len(capacity.reports), 2)
                self.assertFalse(executor._active)
        self.assertTrue(client.is_closed)

    async def test_jev_preserves_original_cause_when_cancel_collected_first(self):
        async with asyncio.timeout(5):
            both_started, collected, sibling_closed = (asyncio.Event() for _ in range(3))
            capacity = _HeldFailureCapacity()
            executor = ConcurrentExecutor(capacity)
            original = fatal_error()
            started = set()
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)

            async def system_one(*, state, questions):
                del questions
                page = state['target_page']['page_index']
                started.add(page)
                if len(started) == 2:
                    both_started.set()
                await both_started.wait()
                if page == 1:
                    raise original
                try:
                    await asyncio.Event().wait()
                finally:
                    sibling_closed.set()

            def collect(tasks):
                error = task_group_error(tasks)
                if error is original and capacity.failure_releasing.is_set():
                    collected.set()
                return error

            client.system_one = system_one
            with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client), \
                    patch('pdf_craft.jev.task_group_error', side_effect=collect):
                async with JEVRuntime(JEV('test'), executor) as runtime:
                    consumer = asyncio.create_task(runtime.evaluate_many([
                        (page, {'state': {'target_page': {'page_index': page}}, 'questions': {}})
                        for page in (1, 2)
                    ]))
                    try:
                        await collected.wait()
                        await sibling_closed.wait()
                        self.assertTrue(sibling_closed.is_set())
                    finally:
                        capacity.allow_release.set()
                    with self.assertRaises(NonContinuableError) as raised:
                        await consumer
                    self.assertIs(raised.exception, original)
                    self.assertEqual(exception_graph(raised.exception), [402])
                    self.assertEqual(len(capacity.reports), 2)
            client.__aexit__.assert_awaited_once()

    async def test_translation_window_keeps_first_error_across_request_pools(self):
        for first_index in (0, 1):
            with self.subTest(first_index=first_index):
                async with asyncio.timeout(5):
                    await self._window_race(first_index)

    async def _window_race(self, first_index):
        capacity = _HeldFailureCapacity()
        pools = (ConcurrentExecutor(capacity), ConcurrentExecutor(FixedCapacity(1)))
        first, second = fatal_error(402), fatal_error(429)
        all_started, second_exited, collected = (asyncio.Event() for _ in range(3))
        starts = set()

        async def execute(index):
            async def request():
                starts.add(index)
                if len(starts) == 2:
                    all_started.set()
                await all_started.wait()
                if index == first_index:
                    raise first
                await capacity.failure_releasing.wait()
                raise second
            try:
                return await pools[0 if index == first_index else 1].run(request)
            finally:
                if index != first_index:
                    second_exited.set()

        async def consume():
            try:
                return [item async for item in run_async_parameters([0, 1], execute, 2)]
            finally:
                collected.set()

        # The second request finishes first while the first error's release is
        # blocked. Release it only after the secondary failure has unwound.
        consumer = asyncio.create_task(consume())
        try:
            await second_exited.wait()
        finally:
            capacity.allow_release.set()
        with self.assertRaises(NonContinuableError) as raised:
            await consumer
        self.assertTrue(collected.is_set())
        self.assertIs(raised.exception, first)
        self.assertEqual(exception_graph(raised.exception), [402])
        self.assertFalse(any(pool._active for pool in pools))

    async def test_executor_competing_fatals_always_use_first_recorded_error(self):
        async with asyncio.timeout(5):
            executor = ConcurrentExecutor(FixedCapacity(2))
            started, ready = asyncio.Event(), asyncio.Event()
            first, second = fatal_error(402), fatal_error(429)

            async def origin():
                await started.wait()
                raise first

            async def sibling():
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    ready.set()
                    raise second  # pylint: disable=raise-missing-from

            tasks = [asyncio.create_task(executor.run(origin)), asyncio.create_task(executor.run(sibling))]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.assertTrue(ready.is_set())
            self.assertIs(executor._terminal_error, first)
            self.assertTrue(all(result is first for result in results))
            self.assertEqual(exception_graph(first), [402])

    async def test_fatal_precedes_capacity_close_and_release_failures(self):
        for fail_close in (False, True):
            with self.subTest(fail_close=fail_close):
                original = fatal_error()

                class Provider:
                    async def acquire(self):
                        return self

                    async def release(self, report):
                        del report
                        raise RuntimeError('release failed')

                    async def close(self, error):
                        del error
                        if fail_close:
                            raise RuntimeError('close failed')

                executor = ConcurrentExecutor(Provider())

                async def request():
                    raise original

                with self.assertRaises(NonContinuableError) as raised:
                    await executor.run(request)
                self.assertIs(raised.exception, original)
                self.assertEqual(exception_graph(original), [402])
                self.assertFalse(executor._active)

    async def test_map_keeps_fatal_through_release_and_iterator_close_failure(self):
        original = fatal_error()

        class Provider:
            async def acquire(self):
                return self

            async def close(self, error):
                del error
                return None

            async def release(self, report):
                if report.error is original:
                    raise RuntimeError('release failed')

        async def request(index):
            del index
            raise original

        def operations():
            try:
                yield request
            finally:
                raise RuntimeError('iterator close failed')

        async with asyncio.timeout(5):
            with self.assertRaises(NonContinuableError) as raised:
                async for _ in ConcurrentExecutor(Provider()).map(operations()):
                    self.fail('fatal must not produce a successful result')
            self.assertIs(raised.exception, original)
            self.assertEqual(exception_graph(original), [402])

    async def test_no_fatal_preserves_user_and_self_cancellation(self):
        for user_cancel in (True, False):
            with self.subTest(user_cancel=user_cancel):
                started, cleaned = asyncio.Event(), asyncio.Event()

                async def execute(_index):
                    started.set()
                    try:
                        if not user_cancel:
                            raise asyncio.CancelledError('self cancellation')
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()

                async def consume():
                    return [item async for item in run_async_parameters([1], execute, 1)]

                async with asyncio.timeout(5):
                    task = asyncio.create_task(consume())
                    await started.wait()
                    if user_cancel:
                        task.cancel('user cancellation')
                    with self.assertRaisesRegex(asyncio.CancelledError, 'user cancellation' if user_cancel else 'self cancellation'):
                        await task
                    self.assertTrue(cleaned.is_set())

    async def test_stream_boundary_does_not_leak_into_consumer(self):
        first, unrelated = fatal_error(402), fatal_error(429)

        @fatal_error_boundary
        async def stream():
            yield 'first'
            raise first

        @fatal_error_boundary
        async def other_operation():
            raise unrelated

        source = stream()
        self.assertEqual(await anext(source), 'first')
        with self.assertRaises(NonContinuableError) as other:
            await other_operation()
        self.assertIs(other.exception, unrelated)
        with self.assertRaises(NonContinuableError) as raised:
            await anext(source)
        self.assertIs(raised.exception, first)
        self.assertEqual(exception_graph(first), [402])

    async def test_pipeline_cleanup_error_cannot_replace_observed_fatal(self):
        original = fatal_error()

        @fatal_error_boundary
        async def pipeline():
            try:
                async def request():
                    raise original
                await ConcurrentExecutor(FixedCapacity(1)).run(request)
            finally:
                raise RuntimeError('pipeline cleanup failed')

        with self.assertRaises(NonContinuableError) as raised:
            await pipeline()
        self.assertIs(raised.exception, original)
        self.assertEqual(exception_graph(original), [402])

    async def test_llm_existing_sdk_error_is_not_raised_from_itself(self):
        from pdf_craft.llm import LLM, runtime_for
        original = fatal_error()
        runtime = runtime_for(LLM('test', 'https://llm.invalid', 'test', 'o200k_base', retry_times=0),
                              ConcurrentExecutor(FixedCapacity(1)))
        with patch.object(runtime, '_invoke_for_request', AsyncMock(side_effect=original)):
            with self.assertRaises(NonContinuableError) as raised:
                await runtime.request('text', use_cache=False)
        self.assertIs(raised.exception, original)
        self.assertEqual(exception_graph(original), [402])

    async def test_pcex_content_window_propagates_fatal_and_closes_input(self):
        from pdf_craft.document.content import ContentItem
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
        from pdf_craft.transformer.content import translate_content
        original = fatal_error()
        pool = ConcurrentExecutor(FixedCapacity(2))
        started, closed = asyncio.Event(), asyncio.Event()
        active = set()

        class Transformer:
            async def transform(self, chapter, **_kwargs):
                async def request():
                    active.add(chapter.id)
                    if len(active) == 2:
                        started.set()
                    await started.wait()
                    if chapter.id == 1:
                        raise original
                    await asyncio.Event().wait()
                return await pool.run(request)

        async def items():
            try:
                for index in (1, 2):
                    yield ContentItem(f'chapter_{index}.xml', index, -1, 0,
                                      TextFlowItem('body', -1, [SourceTextFragment(index, 0, (1, 1, 10, 10), ['Text'])]))
            finally:
                closed.set()

        async with asyncio.timeout(5):
            with self.assertRaises(NonContinuableError) as raised:
                async for _ in translate_content(items(), Transformer(), 2, [], []):
                    self.fail('fatal must not yield successful content')
        self.assertIs(raised.exception, original)
        self.assertTrue(closed.is_set())
        self.assertFalse(pool._active)
        self.assertEqual(exception_graph(original), [402])

    async def test_repeated_cancellation_cannot_interrupt_fatal_group_cleanup(self):
        from pdf_craft.concurrency import settle_tasks
        original = fatal_error()
        running, cleaning, finish_cleanup, cleaned = (asyncio.Event() for _ in range(4))

        async def sibling():
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish_cleanup.wait()
                cleaned.set()

        @fatal_error_boundary
        async def operation():
            task = asyncio.create_task(sibling())
            try:
                await running.wait()
                async def request():
                    raise original
                await ConcurrentExecutor(FixedCapacity(1)).run(request)
            finally:
                task.cancel()
                await settle_tasks([task])

        async with asyncio.timeout(5):
            task = asyncio.create_task(operation())
            await cleaning.wait()
            task.cancel('cancel again while cleaning')
            finish_cleanup.set()
            with self.assertRaises(NonContinuableError) as raised:
                await task
        self.assertIs(raised.exception, original)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(exception_graph(original), [402])

    async def test_map_close_failure_always_delivers_fatal_and_settles_producer(self):
        import gc
        for fail_release in (False, True):
            with self.subTest(fail_release=fail_release):
                async with asyncio.timeout(5):
                    original = fatal_error()
                    producer_waiting, producer_settled, released = (asyncio.Event() for _ in range(3))
                    loop = asyncio.get_running_loop()
                    previous_handler = loop.get_exception_handler()
                    unhandled = []
                    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

                    class BrokenCapacity(FixedCapacity):
                        def __init__(self):
                            super().__init__(1)
                            self.acquisitions = 0

                        async def acquire(self):
                            self.acquisitions += 1
                            waiting = self.acquisitions > 1
                            if waiting:
                                producer_waiting.set()
                            try:
                                return await super().acquire()
                            finally:
                                if waiting:
                                    producer_settled.set()

                        async def close(self, error):
                            del error
                            raise RuntimeError('capacity close failed')

                        async def release(self, report):
                            released.set()
                            if fail_release:
                                raise RuntimeError('lease release failed')
                            await super().release(report)

                    executor = ConcurrentExecutor(BrokenCapacity())

                    async def request(index):
                        del index
                        await producer_waiting.wait()
                        raise original

                    async def consume():
                        return [item async for item in executor.map([request])]

                    consumer = asyncio.create_task(consume())
                    try:
                        # No external cancellation is needed to deliver the
                        # fatal error, even if the provider never returns its
                        # permit and leaves acquire() blocked.
                        done, _ = await asyncio.wait([consumer], timeout=2)
                        self.assertIn(consumer, done, 'map waited for external cancellation')
                        with self.assertRaises(NonContinuableError) as raised:
                            await consumer
                        self.assertIs(raised.exception, original)
                        self.assertEqual(exception_graph(original), [402])
                        self.assertTrue(producer_settled.is_set())
                        self.assertTrue(released.is_set())
                        self.assertFalse(executor._active)
                        consumer = None
                        done.clear()
                        del executor, original, raised
                        gc.collect()
                        barrier = asyncio.Event()
                        loop.call_soon(barrier.set)
                        await barrier.wait()
                        self.assertEqual(unhandled, [])
                    finally:
                        if consumer is not None and not consumer.done():
                            consumer.cancel()
                            await asyncio.gather(consumer, return_exceptions=True)
                        loop.set_exception_handler(previous_handler)

    async def test_map_observes_worker_error_outside_completion_delivery(self):
        from pdf_craft.concurrency import OperationError
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

        class BrokenOperationError(OperationError):
            def for_operation(self, operation_id):
                del operation_id
                raise RuntimeError('worker bookkeeping failed')

        async def request(index):
            del index
            raise BrokenOperationError('operation failed')

        executor = ConcurrentExecutor(FixedCapacity(1))
        try:
            async with asyncio.timeout(5):
                with self.assertRaisesRegex(RuntimeError, 'worker bookkeeping failed'):
                    async for _ in executor.map([request]):
                        self.fail('worker failure must not produce success')
            self.assertFalse(executor._active)
            barrier = asyncio.Event()
            loop.call_soon(barrier.set)
            await barrier.wait()
            self.assertEqual(unhandled, [])
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_translation_window_close_failure_wakes_consumer_with_fatal(self):
        from contextlib import asynccontextmanager
        from pdf_craft.transformer.xml_translator.xml_translator import concurrency as window
        for asynchronous in (False, True):
            for status in (402, 429):
                for fail_settle in (False, True):
                    with self.subTest(asynchronous=asynchronous, status=status, fail_settle=fail_settle):
                        original = fatal_error(status)
                        recorded, closed, settled = (asyncio.Event() for _ in range(3))
                        real_settle, real_directory = window.settle_tasks, window.temporary_directory
                        directories = []

                        class Capacity(FixedCapacity):
                            async def release(self, report):
                                if report.error is original:
                                    recorded.set()  # _terminate has already recorded the cause.
                                await super().release(report)

                        pool = ConcurrentExecutor(Capacity(1))

                        def sync_input():
                            try:
                                yield 1
                            finally:
                                self.assertTrue(recorded.is_set())
                                closed.set()
                                raise RuntimeError('input close failed')

                        async def async_input():
                            try:
                                yield 1
                            finally:
                                self.assertTrue(recorded.is_set())
                                closed.set()
                                raise RuntimeError('input aclose failed')

                        async def settle(tasks):
                            await real_settle(tasks)
                            settled.set()
                            if fail_settle:
                                raise RuntimeError('task settling failed')

                        @asynccontextmanager
                        async def directory(prefix):
                            async with real_directory(prefix) as path:
                                directories.append(Path(path))
                                yield path

                        async def execute(_value):
                            async def request():
                                raise original
                            return await pool.run(request)

                        async def consume():
                            source = async_input() if asynchronous else sync_input()
                            return [item async for item in window.run_async_parameters(source, execute, 1)]

                        with patch.object(window, 'settle_tasks', side_effect=settle), \
                                patch.object(window, 'temporary_directory', side_effect=directory):
                            consumer = asyncio.create_task(consume())
                            try:
                                await asyncio.wait_for(closed.wait(), 2)
                                done, _ = await asyncio.wait([consumer], timeout=2)
                                self.assertIn(consumer, done, 'consumer required external cancellation after input close failed')
                                with self.assertRaises(NonContinuableError) as raised:
                                    await consumer
                                self.assertIs(raised.exception, original)
                                self.assertEqual(exception_graph(original), [status])
                                self.assertTrue(settled.is_set())
                                self.assertFalse(pool._active)
                                self.assertTrue(directories)
                                self.assertTrue(all(not path.exists() for path in directories))
                            finally:
                                if not consumer.done():
                                    consumer.cancel()
                                    await asyncio.gather(consumer, return_exceptions=True)
