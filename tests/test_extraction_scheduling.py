"""Render admission waits must not own workers or obstruct cancellation."""
# pylint: disable=cell-var-from-loop,protected-access
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft import DeepSeekOCRVendorConfig
from pdf_craft.pdf.ocr import OCR, OCREventKind
from tests.test_vendor_ocr_concurrency import _Handler, _SequentialExecutor


class ExtractionSchedulingTests(IsolatedAsyncioTestCase):
    async def test_cancel_while_render_admission_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        rendered = []
        ocr = OCR(DeepSeekOCRVendorConfig(
            base_url='https://example.invalid/v1', api_key='key', model='model'),
            cast(Any, _Handler(rendered)))

        async def admission(event):
            if event.kind == OCREventKind.START:
                entered.set()
                await release.wait()

        async def consume(root):
            async for _ in ocr.recognize_vendor(
                    _SequentialExecutor(), pdf_path=root / 'source.pdf',
                    asset_path=root / 'assets', ocr_path=root / 'ocr',
                    on_preparation_event=admission):
                pass

        with TemporaryDirectory() as directory, patch.object(
                ocr, '_recognize_vendor_page', side_effect=AssertionError('no admitted page')):
            task = asyncio.create_task(consume(Path(directory)))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                done, _ = await asyncio.wait([task], timeout=.3)
                self.assertIn(task, done, 'cancellation waits on its own admission callback')
                self.assertTrue(task.cancelled())
                self.assertEqual(rendered, [])
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_full_refinement_window_cancels_or_propagates_provider_failure(self):
        # Use the real render/admission/cleanup owners. Hold all OCR completions
        # until the refinement input window is full, then stop the consumer.
        from contextlib import asynccontextmanager
        from pdf_craft import NonContinuableError
        from pdf_craft.common import stream as stream_module
        from pdf_craft.extractor.chapter import live as live_module
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from pdf_craft.extractor.refinement import MAX_INPUT_LEAD
        from pdf_craft.pdf import ocr as ocr_module
        from pdf_craft import runtime
        from pdf_craft.runtime import ExecutionDomain
        from tests.test_vendor_ocr_concurrency import _Document

        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                return target
            yield process

        class Document(_Document):
            pages_count = MAX_INPUT_LEAD + 3

        class Handler:
            def open(self, _path):
                return Document(rendered)

        for failing in (False, True):
            with self.subTest(provider_failure=failing), TemporaryDirectory() as directory:
                root = Path(directory)
                rendered = []
                blocked = asyncio.Event()
                failure = NonContinuableError('provider stopped')
                domain = ExecutionDomain('scheduling-test', 1)
                async def recognize(_runtime, prepared, **_kwargs):
                    async def drain():
                        async for _ in prepared:
                            pass
                    feeder = asyncio.create_task(drain())
                    try:
                        await blocked.wait()
                        if failing:
                            raise failure
                        await asyncio.Event().wait()
                        yield  # Async generator; no OCR completion is published.
                    finally:
                        feeder.cancel()
                        await asyncio.gather(feeder, return_exceptions=True)

                with patch.object(runtime, 'IO_DOMAIN', domain), patch.object(
                        stream_module, 'IO_DOMAIN', domain), patch.object(
                        live_module, 'IO_DOMAIN', domain), patch.object(ocr_module, 'IO_DOMAIN', domain):
                    live = LiveExtraction(root / 'live', root / 'ocr', True, refinement=factory)
                    async def admission(event):
                        if event.kind == OCREventKind.START and event.page_index == MAX_INPUT_LEAD + 1:
                            blocked.set()
                        await live.event(event)
                    ocr = OCR(DeepSeekOCRVendorConfig(
                        base_url='https://example.invalid/v1', api_key='key', model='model'), cast(Any, Handler()))
                    async def consume():
                        try:
                            async for _ in ocr.recognize_vendor(
                                    _SequentialExecutor(), pdf_path=root / 'source.pdf',
                                    asset_path=root / 'assets', ocr_path=root / 'ocr',
                                    on_preparation_event=admission):
                                pass
                        finally:
                            await live.cancel()
                    with patch.object(ocr, '_recognize_vendor_pages', recognize), patch.object(
                            ExecutionDomain, '__init__', side_effect=AssertionError('private extraction pool')):
                        task = asyncio.create_task(consume())
                        try:
                            await asyncio.wait_for(blocked.wait(), 3)
                            if not failing:
                                task.cancel()
                            done, _ = await asyncio.wait([task], timeout=1)
                            self.assertIn(task, done, 'cleanup depends on an outer live.cancel()')
                            if failing:
                                self.assertIs(task.exception(), failure)
                            else:
                                self.assertTrue(task.cancelled())
                            self.assertEqual(rendered, list(range(1, MAX_INPUT_LEAD + 1)))
                            self.assertTrue(live.task.done())
                        finally:
                            await live.cancel()
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                domain.close()

    async def test_real_request_many_cancel_at_full_refinement_window(self):
        await self._check_real_request_many_teardown(provider_failure=False)

    async def test_real_request_many_fatal_at_full_refinement_window(self):
        await self._check_real_request_many_teardown(provider_failure=True)

    async def _check_real_request_many_teardown(self, *, provider_failure):
        # Only the native PDF and HTTP transport boundaries are fake. The OCR
        # queue, request_many, executor leases and all cleanup owners are real.
        from contextlib import ExitStack, asynccontextmanager
        import tempfile
        import httpx
        from pdf_craft import ConcurrentExecutor, FixedCapacity, runtime
        from pdf_craft.common import stream as stream_module
        from pdf_craft.error import OCRBillingError
        from pdf_craft.extractor.chapter import live as live_module
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from pdf_craft.extractor.refinement import MAX_INPUT_LEAD
        from pdf_craft.pdf import ocr as ocr_module, vendor_ocr as vendor_module
        from pdf_craft.runtime import ExecutionDomain
        from tests.test_vendor_ocr_concurrency import _Document

        blocked, requests_ready, fail_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
        rendered, closed, temporary_paths = [], [], []
        started, settled = [], []
        active = 0
        temporary_directory = tempfile.TemporaryDirectory

        def track_temporary(*args, **kwargs):
            temporary = temporary_directory(*args, **kwargs)
            temporary_paths.append(Path(temporary.name))
            return temporary

        class Document(_Document):
            pages_count = MAX_INPUT_LEAD + 3

            def close(self):
                closed.append(True)

        class Handler:
            def open(self, _path):
                return Document(rendered)

        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                raise AssertionError('OCR has not completed a page')
            yield process

        async def transport(request):
            nonlocal active
            ordinal = len(started)
            started.append(request)
            active += 1
            if active == 2:
                requests_ready.set()
            try:
                if ordinal == 0 and provider_failure:
                    await fail_first.wait()
                    return httpx.Response(402, request=request, json={'error': 'payment required'})
                await asyncio.Event().wait()
                raise AssertionError('held request unexpectedly resumed')
            finally:
                active -= 1
                settled.append(ordinal)

        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        domain = ExecutionDomain('request-many-teardown-test', 1)
        task = None
        live = None
        baseline_tasks = asyncio.all_tasks()
        with TemporaryDirectory() as directory, ExitStack() as patches:
            root = Path(directory)
            for module in (runtime, stream_module, live_module, ocr_module, vendor_module):
                patches.enter_context(patch.object(module, 'IO_DOMAIN', domain))
            patches.enter_context(patch.object(runtime, 'TemporaryDirectory', side_effect=track_temporary))
            patches.enter_context(patch.object(vendor_module.httpx, 'AsyncClient', return_value=client))
            try:
                live = LiveExtraction(root / 'live', root / 'ocr', True, refinement=factory)

                async def admission(event):
                    if event.kind == OCREventKind.START and event.page_index == MAX_INPUT_LEAD + 1:
                        blocked.set()
                    await live.event(event)

                ocr = OCR(DeepSeekOCRVendorConfig(
                    base_url='https://example.invalid/v1', api_key='key', model='model', retry_times=0),
                    cast(Any, Handler()))

                async def consume():
                    try:
                        async for _ in ocr.recognize_vendor(
                                ConcurrentExecutor(FixedCapacity(2)),
                                pdf_path=root / 'source.pdf', asset_path=root / 'assets',
                                ocr_path=root / 'ocr', includes_footnotes=True,
                                on_preparation_event=admission):
                            self.fail('held OCR requests must not publish a completion')
                    finally:
                        await live.cancel()

                task = asyncio.create_task(consume())
                await asyncio.wait_for(asyncio.gather(blocked.wait(), requests_ready.wait()), 3)
                self.assertFalse(task.done())
                self.assertEqual(active, 2)
                self.assertEqual(rendered, list(range(1, MAX_INPUT_LEAD + 1)))
                self.assertEqual(live._consumed, 0)
                if provider_failure:
                    fail_first.set()
                else:
                    task.cancel()
                done, _ = await asyncio.wait([task], timeout=2)
                self.assertIn(task, done, 'real request_many cannot settle a full-window teardown')
                if provider_failure:
                    error = task.exception()
                    self.assertIsInstance(error, OCRBillingError)
                    self.assertEqual(cast(OCRBillingError, error).page_index, 1)
                    self.assertIsNotNone(cast(OCRBillingError, error).__cause__)
                else:
                    self.assertTrue(task.cancelled())
                self.assertEqual(active, 0)
                self.assertEqual(sorted(settled), [0, 1])
                self.assertEqual(closed, [True])
                self.assertTrue(client.is_closed)
                self.assertTrue(live.task.done())
                self.assertEqual(len(temporary_paths), 2)
                self.assertTrue(all(not path.exists() for path in temporary_paths))
                self.assertEqual(asyncio.all_tasks() - baseline_tasks, set(), 'pipeline child tasks leaked')
            finally:
                # Also release the original implementation's circular wait if
                # this regression test fails, so it cannot strand test workers.
                if live is not None:
                    await live.cancel()
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await client.aclose()
                domain.close()
