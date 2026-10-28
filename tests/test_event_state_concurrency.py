"""Controlled event interleavings; no OCR, model calls or user archives."""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft._progress import OperationProgress
from pdf_craft.common import save_xml
from pdf_craft.common.stream import DiskStream
from pdf_craft.extractor.chapter.live import LiveExtraction
from pdf_craft.pdf import encode
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.progress import OperationKind, ProgressEventKind, UnitKind
from pdf_craft.runtime import ExecutionDomain, IO_DOMAIN
from tests.test_stream_callbacks import page
import pdf_craft.extractor.chapter.live as live_module


def event(kind, index):
    return OCREvent(kind, index, 4)


class EventStateConcurrencyTests(IsolatedAsyncioTestCase):
    async def test_complete_and_next_start_consume_page_once(self):
        entered, release, competing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_read = live_module.read_xml

        class GatedIO:
            async def run(self, function, *args, **kwargs):
                if function is original_read:
                    entered.set()
                    await release.wait()
                return await IO_DOMAIN.run(function, *args, **kwargs)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(page(1)), root / 'page_1.xml')
            live = LiveExtraction(root / 'live', root, False)
            try:
                await live.event(event(OCREventKind.START, 1))
                with patch.object(live_module, 'IO_DOMAIN', GatedIO()):
                    first = asyncio.create_task(live.event(event(OCREventKind.COMPLETE, 1)))
                    await entered.wait()
                    async def start():
                        competing.set()
                        await live.event(event(OCREventKind.START, 2))
                    second = asyncio.create_task(start())
                    await competing.wait()
                    release.set()
                    results = await asyncio.gather(first, second, return_exceptions=True)
                self.assertEqual(results, [None, None])
                await live.event(event(OCREventKind.IGNORE, 2))
                await live.finish()
                self.assertEqual([p.index for p in live.pages.read()], [1])
                self.assertEqual(live.position, 2)
            finally:
                release.set()
                await live.cancel()

    async def test_waiting_analysis_does_not_exhaust_page_io(self):
        domain = ExecutionDomain('event-test', 2)
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        readers = 0
        original = DiskStream.read
        def read(stream):
            nonlocal readers
            readers += 1
            if readers == 2:
                loop.call_soon_threadsafe(entered.set)
            yield from original(stream)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(page(1)), root / 'page_1.xml')
            with patch.object(live_module, 'IO_DOMAIN', domain), patch.object(DiskStream, 'read', read):
                lives = [LiveExtraction(root / str(i), root, False) for i in range(2)]
                pending = None
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    await lives[0].event(event(OCREventKind.START, 1))
                    pending = asyncio.create_task(lives[0].event(event(OCREventKind.COMPLETE, 1)))
                    await asyncio.wait_for(asyncio.shield(pending), 1)
                    self.assertEqual(lives[0].position, 1)
                finally:
                    # Release consumers even against the old deadlocking code.
                    lives[1].pages.close()
                    if pending is not None:
                        await pending
                    for live in lives:
                        await live.cancel()
        domain._executor.shutdown(wait=True)

    async def test_concurrent_finish_settles_unit_once(self):
        entered, release, competing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []
        async def callback(value):
            events.append(value)
            if value.unit_id is not None and value.kind == ProgressEventKind.PROGRESS:
                entered.set()
                await release.wait()
        progress = OperationProgress(callback, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 1}, total_words=2)
        unit = await progress.unit(UnitKind.CONTENT, words=2)
        first = asyncio.create_task(unit.finish())
        await entered.wait()
        async def finish():
            competing.set()
            await unit.finish()
        second = asyncio.create_task(finish())
        await competing.wait()
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(progress.finished[UnitKind.CONTENT], 1)
        self.assertEqual(sum(e.kind == ProgressEventKind.COMPLETED for e in events), 1)
        self.assertEqual(progress.completed_words, 2)
        await progress.finish()

    async def test_cancel_can_interrupt_finish_waiting_for_callback(self):
        entered = asyncio.Event()
        async def callback(_):
            entered.set()
            await asyncio.Event().wait()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            live = LiveExtraction(root / 'live', root, False, callback)
            await live.deliveries[0].append('pending')
            await entered.wait()
            finishing = asyncio.create_task(live.finish())
            # Wait for analysis EOF, so finish is now draining the blocked callback.
            await asyncio.wait_for(asyncio.shield(live.task), 3)
            cancelling = asyncio.create_task(live.cancel())
            try:
                await asyncio.wait_for(asyncio.shield(cancelling), 1)
                self.assertTrue(live.deliveries[0]._task.done())
            finally:
                finishing.cancel()
                await asyncio.gather(finishing, return_exceptions=True)
                await cancelling

    async def test_out_of_order_skip_failed_ignore_and_cache(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index in (1, 3, 4):
                save_xml(encode(page(index)), root / f'page_{index}.xml')
            live = LiveExtraction(root / 'live', root, False)
            try:
                for index in range(1, 5):
                    await live.event(event(OCREventKind.START, index))
                await asyncio.gather(
                    live.event(event(OCREventKind.FAILED, 4)),
                    live.event(event(OCREventKind.SKIP, 3)),
                    live.event(event(OCREventKind.IGNORE, 2)),
                    live.event(event(OCREventKind.COMPLETE, 1)))
                await live.finish()
                self.assertEqual([p.index for p in live.pages.read()], [1, 3, 4])
                self.assertEqual(live.position, 4)
                self.assertEqual(live.ready, set())
                self.assertEqual(live.ignored, set())
            finally:
                await live.cancel()
            cached = LiveExtraction(root / 'cache', root, False)
            try:
                await asyncio.gather(cached.finish(), cached.finish())
                self.assertEqual([p.index for p in cached.pages.read()], [1, 3, 4])
            finally:
                await cached.cancel()

    async def test_finish_callback_failure_is_not_retried_or_counted_twice(self):
        calls = 0
        async def callback(value):
            nonlocal calls
            if value.unit_id is not None and value.kind == ProgressEventKind.PROGRESS:
                calls += 1
                raise ValueError('callback failed')
        progress = OperationProgress(callback, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 1}, total_words=2)
        unit = await progress.unit(UnitKind.CONTENT, words=2)
        results = await asyncio.gather(unit.finish(), unit.finish(), return_exceptions=True)
        self.assertTrue(all(isinstance(error, Exception) for error in results))
        self.assertEqual(calls, 1)
        self.assertEqual(progress.finished[UnitKind.CONTENT], 0)
        self.assertEqual(progress.completed_words, 2)

    async def test_cancelled_completion_cannot_later_become_success(self):
        entered = asyncio.Event()
        events = []
        async def callback(value):
            events.append(value)
            if value.unit_id is not None and value.kind == ProgressEventKind.PROGRESS:
                entered.set()
                await asyncio.Event().wait()
        progress = OperationProgress(callback, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 1}, total_words=2)
        unit = await progress.unit(UnitKind.CONTENT, words=2)
        first = asyncio.create_task(unit.finish())
        await entered.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        with self.assertRaises(asyncio.CancelledError):
            await unit.finish()
        self.assertEqual(progress.finished[UnitKind.CONTENT], 0)
        self.assertFalse(any(e.kind == ProgressEventKind.COMPLETED for e in events))

    async def test_progress_then_finish_keeps_words_and_event_order(self):
        entered, release, competing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        events = []
        async def callback(value):
            events.append(value)
            if value.unit_id is not None and value.kind == ProgressEventKind.PROGRESS:
                entered.set()
                await release.wait()
        progress = OperationProgress(callback, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 1}, total_words=2)
        unit = await progress.unit(UnitKind.CONTENT, words=2)
        advancing = asyncio.create_task(unit.progress(stage=1, words=1))
        await entered.wait()
        async def finish():
            competing.set()
            await unit.finish()
        finishing = asyncio.create_task(finish())
        await competing.wait()
        release.set()
        await asyncio.gather(advancing, finishing)
        updates = [e for e in events if e.unit_id is not None and e.kind == ProgressEventKind.PROGRESS]
        self.assertEqual([(e.stage, e.unit_completed_words) for e in updates], [(1, 1), (2, 2)])
        await progress.finish()

    async def test_cancelled_committed_append_is_never_replayed(self):
        committed = asyncio.Event()
        original_read = live_module.read_xml
        class GatedIO:
            async def run(self, function, *args, **kwargs):
                result = await IO_DOMAIN.run(function, *args, **kwargs)
                if getattr(function, '__name__', '') == 'append':
                    committed.set()
                    await asyncio.Event().wait()
                return result
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(page(1)), root / 'page_1.xml')
            live = LiveExtraction(root / 'live', root, False)
            try:
                await live.event(event(OCREventKind.START, 1))
                with patch.object(live_module, 'IO_DOMAIN', GatedIO()):
                    consuming = asyncio.create_task(live.event(event(OCREventKind.COMPLETE, 1)))
                    await committed.wait()
                    consuming.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await consuming
                    with self.assertRaises(asyncio.CancelledError):
                        await live.event(event(OCREventKind.START, 2))
                self.assertEqual(live.pages._count, 1)
                self.assertIs(live_module.read_xml, original_read)
            finally:
                await live.cancel()
            self.assertTrue(live.task.done())

    async def test_storage_failure_stops_later_events_without_duplicate_append(self):
        for operation in ('read', 'append'):
            with self.subTest(operation=operation), TemporaryDirectory() as directory:
                root = Path(directory)
                save_xml(encode(page(1)), root / 'page_1.xml')
                live = LiveExtraction(root / 'live', root, False)
                failure = OSError('storage unavailable')
                target, attribute = ((live_module, 'read_xml') if operation == 'read' else (live.pages, 'append'))
                try:
                    await live.event(event(OCREventKind.START, 1))
                    with patch.object(target, attribute, side_effect=failure):
                        with self.assertRaises(OSError) as raised:
                            await live.event(event(OCREventKind.COMPLETE, 1))
                    self.assertIs(raised.exception, failure)
                    with self.assertRaises(OSError) as repeated:
                        await live.event(event(OCREventKind.START, 2))
                    self.assertIs(repeated.exception, failure)
                    self.assertEqual(live.position, 0)
                    self.assertEqual(live.pages._count, 0)
                finally:
                    await live.cancel()

    async def test_corrupt_page_retains_recoverable_analysis_error(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'page_1.xml').write_text('<broken', encoding='utf-8')
            live = LiveExtraction(root / 'live', root, False)
            try:
                await live.event(event(OCREventKind.START, 1))
                await live.event(event(OCREventKind.COMPLETE, 1))
                error = live.analysis_error
                self.assertIsNotNone(error)
                await live.event(event(OCREventKind.START, 2))
                await live.finish()
                self.assertIs(live.analysis_error, error)
                self.assertEqual(getattr(error, 'page_index'), 1)
                self.assertEqual(live.pages._count, 0)
            finally:
                await live.cancel()

    async def test_render_callback_can_use_shared_io_with_one_slot(self):
        # Cached pages exercise the real producer/bridge without network calls.
        from typing import Any, cast
        from pdf_craft import ConcurrentExecutor, FixedCapacity, DeepSeekOCRVendorConfig
        from pdf_craft.pdf.ocr import OCR
        from pdf_craft.pdf import ocr as ocr_module
        from tests.test_vendor_ocr_concurrency import _Handler
        domain = ExecutionDomain('render-test', 1)
        callback_task = None
        worker_task = None
        entered = asyncio.Event()
        async def callback(_):
            nonlocal callback_task, worker_task
            if entered.is_set():
                return
            callback_task = asyncio.current_task()
            worker_task = asyncio.create_task(domain.run(lambda: None))
            entered.set()
            # Shield permits cleanup of the old circular wait without leaving
            # a blocked executor thread when the red assertion times out.
            try:
                await asyncio.shield(worker_task)
            except asyncio.CancelledError:
                return
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ocr").mkdir()
            for index in (1, 2, 3):
                save_xml(encode(page(index)), root / 'ocr' / f'page_{index}.xml')
            ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://example.invalid', api_key='x', model='x'),
                      cast(Any, _Handler([])))
            async def consume():
                async for _ in ocr.recognize_vendor(
                        ConcurrentExecutor(FixedCapacity(1)), pdf_path=root/'source.pdf',
                        asset_path=root/'assets', ocr_path=root/'ocr', includes_footnotes=False,
                        on_preparation_event=callback):
                    pass
            with patch.object(ocr_module, 'IO_DOMAIN', domain):
                consumer = asyncio.create_task(consume())
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    await asyncio.wait_for(asyncio.shield(consumer), 1)
                finally:
                    if callback_task is not None and not callback_task.done():
                        callback_task.cancel()
                    await consumer
                    if worker_task is not None:
                        await cast(asyncio.Task, worker_task)
                    domain._executor.shutdown(wait=True)
