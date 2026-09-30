# pylint: disable=protected-access
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, TypeVar, cast
from unittest.mock import AsyncMock, patch

from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor,
    ExtractionOptions, FixedCapacity, NonContinuableError, PDFCraft,
    PDFCraftExtraction, PDFDocumentMetadata, PDFOptions, SubmitKind,
    TranslationEventKind, TranslationEvent,
)
from pdf_craft.craft import _AsyncPDFHandlerBridge
from pdf_craft.common import save_xml
import pdf_craft.document.package as document_package
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.extractor import PDFExtractor
from pdf_craft.llm import LLM, runtime_for
from pdf_craft.runtime import QT_DOMAIN, invoke_callback, run_subprocess
from pdf_craft.pipeline.pdf.text_layout import (
    _ensure_qt_application, _qt_lifecycle_probe, _qt_modules,
)
import pdf_craft.transformer.package as transformer_package
from pdf_craft.transformer.package import ChapterExtractionTransformer
from pdf_craft.transformer.xml_translator.xml_translator.concurrency import (
    run_concurrency_async,
)
from tests.extraction_helpers import make_extraction


_EventT = TypeVar("_EventT")


def _recording_callbacks(
    events: list[_EventT], callback_threads: list[int],
) -> tuple[Callable[[_EventT], None], Callable[[_EventT], Awaitable[None]]]:
    def sync_callback(event: _EventT) -> None:
        events.append(event)
        callback_threads.append(threading.get_ident())

    async def async_callback(event: _EventT) -> None:
        await asyncio.sleep(0)
        events.append(event)
        callback_threads.append(threading.get_ident())

    return sync_callback, async_callback


class _Engine:
    def __init__(self) -> None:
        self.thread_name: str | None = None

    def extract_package(self, *, analysing_path, on_ocr_event, **_kwargs):
        self.thread_name = threading.current_thread().name
        on_ocr_event("page")
        make_extraction(
            analysing_path / "extraction",
            page_pixel_sizes={1: (10, 10)},
        )
        return None, None, None, None, "metering"


class _CancellableEngine:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.observed_cancel = threading.Event()
        self.finished = threading.Event()
        self.late_write_succeeded = False
        self.analysing_path: Path | None = None

    def extract_package(self, *, aborted, analysing_path, **_kwargs):
        self.analysing_path = analysing_path
        self.started.set()
        while not aborted():
            self.observed_cancel.wait(0.01)
        self.observed_cancel.set()
        time.sleep(0.15)
        try:
            (analysing_path / "late-worker-write.txt").write_text(
                "worker still owned workspace", encoding="utf-8",
            )
            self.late_write_succeeded = True
        finally:
            self.finished.set()
        raise RuntimeError("cancelled by cooperative engine")


class _NativeAsyncEngine:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    async def extract_package_async(self, *, analysing_path, **_kwargs):
        self.thread_id = threading.get_ident()
        await asyncio.sleep(0)
        make_extraction(
            analysing_path / "extraction",
            page_pixel_sizes={1: (10, 10)},
        )
        return None, None, None, None, "async-metering"

    def extract_package(self, **_kwargs):
        raise AssertionError("native async extraction must not use the sync hook")


class _AsyncXMLTranslator:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    async def translate_element(self, task, **_kwargs):
        self.thread_id = threading.get_ident()
        await asyncio.sleep(0)
        for element in task.element.iter():
            if element.text:
                element.text = f"async:{element.text}"
        return task.element, task.payload


class _AsyncChapterTransformer:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    async def transform(self, chapter: Chapter) -> Chapter:
        self.thread_id = threading.get_ident()
        await asyncio.sleep(0)
        flow = chapter.flow_items[0]
        assert isinstance(flow, TextFlowItem)
        fragment = flow.children[0]
        assert isinstance(fragment, SourceTextFragment)
        fragment.content = ["native async extension"]
        return chapter


class _SyncChapterTransformer:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    def transform(self, chapter: Chapter) -> Chapter:
        self.thread_id = threading.get_ident()
        return chapter


class _BlockingSyncChapterTransformer:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def transform(self, chapter: Chapter) -> Chapter:
        self.started.set()
        self.release.wait(1)
        self.finished.set()
        return chapter


class _AsyncDocument:
    def __init__(self) -> None:
        self.thread_ids: list[int] = []
        self.closed = False

    async def pages_count(self) -> int:
        self.thread_ids.append(threading.get_ident())
        return 2

    async def metadata(self) -> PDFDocumentMetadata:
        self.thread_ids.append(threading.get_ident())
        return PDFDocumentMetadata(
            title=None, description=None, publisher=None, isbn=None,
            authors=[], editors=[], translators=[],
            modified=datetime.now(timezone.utc),
        )

    async def page_size(self, page_index: int) -> tuple[float, float]:
        del page_index
        self.thread_ids.append(threading.get_ident())
        return 8.5, 11.0

    async def render_page(self, page_index: int, dpi: int) -> Image.Image:
        del page_index, dpi
        self.thread_ids.append(threading.get_ident())
        return Image.new("RGB", (100, 100), "white")

    async def close(self) -> None:
        self.thread_ids.append(threading.get_ident())
        self.closed = True


class _AsyncHandler:
    def __init__(self, document: _AsyncDocument) -> None:
        self.document = document
        self.thread_id: int | None = None
        self.open_count = 0

    async def open(self, pdf_path: Path) -> _AsyncDocument:
        del pdf_path
        self.thread_id = threading.get_ident()
        self.open_count += 1
        return self.document


class TestAsyncAPI(unittest.IsolatedAsyncioTestCase):
    async def test_extraction_io_is_owned_by_async_facade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", language="en")
            craft = AsyncPDFCraft()
            exported = await craft.export_extraction(source, root / "book.pcex")
            opened = await craft.open_extraction(root / "book.pcex")
            self.assertIsInstance(exported, PDFCraftExtraction)
            self.assertIsInstance(opened, PDFCraftExtraction)
            self.assertFalse(hasattr(opened, "open"))
            self.assertFalse(hasattr(opened, "export"))

    async def test_direct_extractor_callbacks_run_on_caller_loop(self):
        loop_thread = threading.get_ident()
        cases = (
            ("extract", False),
            ("extract", True),
            ("extract_with_metering", False),
            ("extract_with_metering", True),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (method_name, is_async) in enumerate(cases):
                with self.subTest(method=method_name, async_callback=is_async):
                    events: list[str] = []
                    callback_threads: list[int] = []
                    sync_callback, async_callback = _recording_callbacks(
                        events, callback_threads,
                    )

                    engine = _Engine()
                    method = getattr(PDFExtractor(engine), method_name)
                    await method(
                        Path("source.pdf"), root / f"book-{index}.pcex",
                        on_ocr_event=async_callback if is_async else sync_callback,
                    )
                    self.assertEqual(events, ["page"])
                    self.assertEqual(callback_threads, [loop_thread])
                    self.assertNotEqual(engine.thread_name, threading.current_thread().name)

    async def test_direct_extractor_prefers_native_async_engine_hook(self):
        engine = _NativeAsyncEngine()
        caller_thread = threading.get_ident()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, metering = await PDFExtractor(engine).extract_with_metering(
                Path("source.pdf"), root / "book.pcex"
            )

        self.assertIsInstance(extraction, PDFCraftExtraction)
        self.assertEqual(metering, "async-metering")
        self.assertEqual(engine.thread_id, caller_thread)

    async def test_direct_async_extractor_checks_existing_output_off_loop(self):
        caller_thread = threading.get_ident()
        check_threads: list[int] = []
        original_exists = Path.exists

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "book.pcex"
            target.write_bytes(b"existing")

            def tracked_exists(path: Path) -> bool:
                if path == target:
                    check_threads.append(threading.get_ident())
                return original_exists(path)

            engine = _NativeAsyncEngine()
            with patch.object(Path, "exists", tracked_exists):
                with self.assertRaisesRegex(
                    FileExistsError, "PDFCraftExtraction already exists",
                ):
                    await PDFExtractor(engine).extract_with_metering(
                        Path("source.pdf"), target,
                    )

        self.assertIsNone(engine.thread_id)
        self.assertEqual(len(check_threads), 1)
        self.assertNotEqual(check_threads[0], caller_thread)

    async def test_direct_async_extractor_creates_and_cleans_workspace_off_loop(self):
        caller_thread = threading.get_ident()
        created_threads: list[int] = []
        cleanup_threads: list[int] = []
        workspace_paths: list[Path] = []

        class TrackedTemporaryDirectory:
            def __init__(self, *args, **kwargs) -> None:
                created_threads.append(threading.get_ident())
                self._temporary = tempfile.TemporaryDirectory(*args, **kwargs)
                self.name = self._temporary.name
                workspace_paths.append(Path(self.name))

            def cleanup(self) -> None:
                cleanup_threads.append(threading.get_ident())
                self._temporary.cleanup()

        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.runtime.TemporaryDirectory",
            TrackedTemporaryDirectory,
        ):
            root = Path(directory)
            extraction, metering = await PDFExtractor(
                _NativeAsyncEngine(),
            ).extract_with_metering(
                Path("source.pdf"), root / "book.pcex",
            )

        self.assertIsInstance(extraction, PDFCraftExtraction)
        self.assertEqual(metering, "async-metering")
        self.assertEqual(len(created_threads), 1)
        self.assertEqual(len(cleanup_threads), 1)
        self.assertNotEqual(created_threads[0], caller_thread)
        self.assertNotEqual(cleanup_threads[0], caller_thread)
        self.assertTrue(workspace_paths)
        self.assertTrue(all(not path.exists() for path in workspace_paths))

    async def test_direct_async_extractor_cancellation_cleans_workspace(self):
        class CancellableNativeAsyncEngine:
            def __init__(self) -> None:
                self.started = asyncio.Event()
                self.analysing_path: Path | None = None

            async def extract_package_async(self, *, analysing_path, **_kwargs):
                self.analysing_path = analysing_path
                self.started.set()
                await asyncio.Event().wait()

            def extract_package(self, **_kwargs):
                raise AssertionError("native async extraction must not use the sync hook")

        engine = CancellableNativeAsyncEngine()
        with tempfile.TemporaryDirectory() as directory:
            task = asyncio.create_task(
                PDFExtractor(engine).extract_with_metering(
                    Path("source.pdf"), Path(directory) / "book.pcex",
                )
            )
            await engine.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        assert engine.analysing_path is not None
        self.assertFalse(engine.analysing_path.exists())

    async def test_cancellation_during_workspace_creation_cleans_off_loop(self):
        caller_thread = threading.get_ident()
        construction_started = threading.Event()
        release_construction = threading.Event()
        cleanup_threads: list[int] = []
        workspace_paths: list[Path] = []

        class BlockingTemporaryDirectory:
            def __init__(self, *args, **kwargs) -> None:
                construction_started.set()
                release_construction.wait(2)
                self._temporary = tempfile.TemporaryDirectory(*args, **kwargs)
                self.name = self._temporary.name
                self._cleaned = False
                workspace_paths.append(Path(self.name))

            def cleanup(self) -> None:
                if self._cleaned:
                    return
                cleanup_threads.append(threading.get_ident())
                self._temporary.cleanup()
                self._cleaned = True

            def __del__(self) -> None:
                if hasattr(self, "_cleaned") and not self._cleaned:
                    self.cleanup()

        engine = _NativeAsyncEngine()
        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.runtime.TemporaryDirectory",
            BlockingTemporaryDirectory,
        ):
            task = asyncio.create_task(
                PDFExtractor(engine).extract_with_metering(
                    Path("source.pdf"), Path(directory) / "book.pcex",
                )
            )
            while not construction_started.is_set():
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            release_construction.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertIsNone(engine.thread_id)
        self.assertEqual(len(cleanup_threads), 1)
        self.assertNotEqual(cleanup_threads[0], caller_thread)
        self.assertEqual(len(workspace_paths), 1)
        self.assertFalse(workspace_paths[0].exists())

    async def test_sync_transformer_callbacks_run_on_caller_loop(self):
        loop_thread = threading.get_ident()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(
                source_root, page_pixel_sizes={1: (10, 10)},
            )
            save_xml(encode(Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["source"])],
            )])), source_root / "chapters" / "chapter_head.xml")

            for index, is_async in enumerate((False, True)):
                with self.subTest(async_callback=is_async):
                    events: list[TranslationEvent] = []
                    callback_threads: list[int] = []
                    sync_callback, async_callback = _recording_callbacks(
                        events, callback_threads,
                    )

                    transformer = _SyncChapterTransformer()
                    await ChapterExtractionTransformer(transformer).transform(
                        source,
                        root / f"target-{index}.pcex",
                        on_translation_event=(
                            async_callback if is_async else sync_callback
                        ),
                        emit_translation_events=True,
                    )
                    self.assertNotEqual(transformer.thread_id, loop_thread)
                    self.assertEqual(set(callback_threads), {loop_thread})
                    self.assertEqual(
                        [event.kind for event in events],
                        [
                            TranslationEventKind.START,
                            TranslationEventKind.ITEM_START,
                            TranslationEventKind.PROGRESS,
                            TranslationEventKind.ITEM_COMPLETE,
                            TranslationEventKind.COMPLETE,
                        ],
                    )

    async def test_export_cancellation_waits_for_writer_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            target = root / "target.pcex"
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            original_write = document_package._write_archive

            def delayed_write(paths, output):
                started.set()
                release.wait(1)
                try:
                    return original_write(paths, output)
                finally:
                    finished.set()

            with patch.object(document_package, "_write_archive", delayed_write):
                task = asyncio.create_task(
                    AsyncPDFCraft().export_extraction(source, target)
                )
                await asyncio.to_thread(started.wait, 1)
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertTrue(finished.is_set())
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(f".{target.name}.*.tmp")), [])

    async def test_export_publication_wins_a_late_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            target = root / "target.pcex"
            published = threading.Event()
            release = threading.Event()
            original_export = source._export_sync

            def pause_after_publication(path, commit):
                result = original_export(path, commit)
                published.set()
                release.wait(1)
                return result

            with patch.object(source, "_export_sync", pause_after_publication):
                task = asyncio.create_task(
                    AsyncPDFCraft().export_extraction(source, target)
                )
                await asyncio.to_thread(published.wait, 1)
                self.assertTrue(target.exists())
                task.cancel()
                release.set()
                exported = await task

            self.assertFalse(task.cancelled())
            self.assertIsNotNone(exported)
            self.assertTrue(target.is_file())

    async def test_transformer_cancellation_cleans_workspace_after_export_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["source"])],
            )])
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_head.xml")
            target = root / "target.pcex"
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            workspace_root: list[Path] = []
            original_write = document_package._write_archive

            def delayed_write(paths, output):
                workspace_root.append(paths.root)
                started.set()
                release.wait(1)
                try:
                    return original_write(paths, output)
                finally:
                    finished.set()

            with patch.object(document_package, "_write_archive", delayed_write):
                task = asyncio.create_task(
                    ChapterExtractionTransformer(_SyncChapterTransformer()).transform(
                        source, target,
                    )
                )
                await asyncio.to_thread(started.wait, 1)
                self.assertTrue(workspace_root[0].exists())
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                self.assertTrue(workspace_root[0].exists())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertTrue(finished.is_set())
            self.assertFalse(workspace_root[0].exists())
            self.assertFalse(target.exists())

    async def test_sync_transformer_cancellation_waits_before_workspace_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["source"])],
            )])
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_head.xml")
            target = root / "target.pcex"
            transformer = _BlockingSyncChapterTransformer()
            workspaces: list[Path] = []
            original_temporary = tempfile.TemporaryDirectory

            def capture_temporary(*args, **kwargs):
                temporary = original_temporary(*args, **kwargs)
                workspaces.append(Path(temporary.name))
                return temporary

            with patch.object(
                transformer_package, "TemporaryDirectory", capture_temporary,
            ):
                task = asyncio.create_task(
                    ChapterExtractionTransformer(transformer).transform(
                        source, target,
                    )
                )
                await asyncio.to_thread(transformer.started.wait, 1)
                self.assertTrue(workspaces[0].exists())
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                self.assertFalse(transformer.finished.is_set())
                self.assertTrue(workspaces[0].exists())
                self.assertFalse(target.exists())
                transformer.release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertTrue(transformer.finished.is_set())
            self.assertFalse(workspaces[0].exists())
            self.assertFalse(target.exists())

    async def test_extraction_keeps_engine_on_ocr_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine()
            callback_threads: list[int] = []

            async def on_ocr_event(_event):
                callback_threads.append(threading.get_ident())

            extraction, metering = await AsyncPDFCraft.from_engine(
                engine
            ).extract_pdf_with_metering(
                "source.pdf",
                Path(directory) / "book.pcex",
                ExtractionOptions(on_ocr_event=on_ocr_event),
            )
            self.assertEqual(metering, "metering")
            self.assertTrue(extraction._validate())
            self.assertIsNotNone(engine.thread_name)
            assert engine.thread_name is not None
            self.assertTrue(engine.thread_name.startswith("pdf-craft-ocr"))
            self.assertEqual(callback_threads, [threading.get_ident()])

    async def test_sync_facade_rejects_an_active_event_loop(self):
        with self.assertRaisesRegex(RuntimeError, "AsyncPDFCraft"):
            PDFCraft().render_markdown("missing.pcex", "missing.md")

    async def test_async_callback_runs_on_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        callback_threads: list[int] = []

        async def callback(_value):
            await asyncio.sleep(0)
            callback_threads.append(threading.get_ident())

        await invoke_callback(callback, object())
        self.assertEqual(callback_threads, [event_loop_thread])

    async def test_bounded_translation_concurrency_preserves_order(self):
        active = 0
        maximum = 0

        async def execute(value: int) -> int:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep((4 - value) * 0.002)
            active -= 1
            return value

        result = [
            value
            async for value in run_concurrency_async(range(4), execute, concurrency=2)
        ]
        self.assertEqual(result, [0, 1, 2, 3])
        self.assertEqual(maximum, 2)

    async def test_translation_window_refills_behind_a_slow_head(self):
        release_head = asyncio.Event()
        third_started = asyncio.Event()

        async def execute(value: int) -> int:
            if value == 0:
                await release_head.wait()
            elif value == 2:
                third_started.set()
            return value

        async def collect():
            return [
                value
                async for value in run_concurrency_async(
                    range(3), execute, concurrency=2,
                )
            ]

        pending = asyncio.create_task(collect())
        await asyncio.wait_for(third_started.wait(), timeout=1)
        release_head.set()
        self.assertEqual(await pending, [0, 1, 2])

    async def test_translation_window_bounds_run_ahead_behind_a_slow_head(self):
        release_head = asyncio.Event()
        third_started = asyncio.Event()
        started: list[int] = []
        pulled = 0

        def parameters():
            nonlocal pulled
            for value in range(1_000):
                pulled += 1
                yield value

        async def execute(value: int) -> int:
            started.append(value)
            if value == 0:
                await release_head.wait()
            elif value == 2:
                third_started.set()
            return value

        stream = run_concurrency_async(parameters(), execute, concurrency=2)
        first = asyncio.ensure_future(anext(stream))
        await asyncio.wait_for(third_started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        self.assertEqual(started, [0, 1, 2])
        self.assertEqual(pulled, 3)
        await asyncio.sleep(0.05)
        self.assertEqual(pulled, 3)

        release_head.set()
        self.assertEqual(await first, 0)
        remaining = [value async for value in stream]
        self.assertEqual(remaining, list(range(1, 1_000)))

    async def test_translation_window_preserves_terminal_error_over_sibling_cancel(self):
        for fatal_index in (0, 3):
            for _ in range(10):
                with self.subTest(fatal_index=fatal_index):
                    await self._assert_terminal_translation_error(fatal_index)

    async def _assert_terminal_translation_error(self, fatal_index: int):
        executor = ConcurrentExecutor(FixedCapacity(4))
        all_started = asyncio.Event()
        never = asyncio.Event()
        started = 0

        async def execute(value: int) -> int:
            async def invoke() -> int:
                nonlocal started
                started += 1
                if started == 4:
                    all_started.set()
                await all_started.wait()
                if value == fatal_index:
                    raise NonContinuableError("quota")
                await never.wait()
                return value

            return await executor.run(invoke)

        with self.assertRaises(NonContinuableError):
            async for _value in run_concurrency_async(
                range(4), execute, concurrency=4,
            ):
                pass

    async def test_cancelling_translation_batch_cancels_pending_tasks(self):
        started = asyncio.Event()
        cancelled = 0

        async def execute(_value: int) -> int:
            nonlocal cancelled
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled += 1
            return 0

        stream = run_concurrency_async(range(3), execute, concurrency=2)
        next_result = asyncio.ensure_future(anext(stream))
        await started.wait()
        next_result.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await next_result
        self.assertEqual(cancelled, 2)

    async def test_llm_transport_is_awaitable(self):
        config = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            retry_times=0,
        )
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))

        async def invoke(*_args):
            await asyncio.sleep(0)
            return "translated"

        runtime._invoke = invoke  # type: ignore[method-assign]
        self.assertEqual(await runtime.request("hello", use_cache=False), "translated")

    async def test_qt_domain_has_stable_thread_affinity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = await QT_DOMAIN.run(_qt_lifecycle_probe, root / "first.pdf")
            second = await QT_DOMAIN.run(_qt_lifecycle_probe, root / "second.pdf")
            self.assertNotEqual(first[0], os.getpid())
            self.assertNotEqual(second[0], os.getpid())
            self.assertNotEqual(first[0], second[0])
            self.assertGreater((root / "first.pdf").stat().st_size, 0)
            self.assertGreater((root / "second.pdf").stat().st_size, 0)

    async def test_qt_process_is_safe_after_main_thread_initialization(self):
        _QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        with tempfile.TemporaryDirectory() as directory:
            identity = await QT_DOMAIN.run(
                _qt_lifecycle_probe, Path(directory) / "isolated.pdf",
            )
            self.assertNotEqual(identity[0], os.getpid())

    async def test_epub_translation_uses_native_async_pipeline(self):
        with patch(
            "pdf_craft.craft.run_epub_translation", new_callable=AsyncMock,
        ) as translate:
            await AsyncPDFCraft().translate_epub(
                "source.epub",
                "target.epub",
                target_language="zh",
                submit=SubmitKind.REPLACE,
            )
        translate.assert_awaited_once()

    async def test_public_sync_epub_entry_rejects_active_loop_before_io(self):
        with patch(
            "pdf_craft.craft.run_epub_translation",
            new_callable=AsyncMock,
        ) as translate:
            with self.assertRaisesRegex(RuntimeError, "active event loop"):
                PDFCraft().translate_epub(
                    "source.epub",
                    "target.epub",
                    target_language="zh",
                    submit=SubmitKind.REPLACE,
                )
        translate.assert_not_called()
        translate.assert_not_awaited()

    async def test_public_sync_epub_entry_delegates_to_async_pipeline(self):
        with patch(
            "pdf_craft.craft.run_epub_translation",
            new_callable=AsyncMock,
        ) as translate:
            await asyncio.to_thread(
                PDFCraft().translate_epub,
                "source.epub",
                "target.epub",
                target_language="zh",
                submit=SubmitKind.REPLACE,
            )
        translate.assert_awaited_once()

    async def test_pcex_xml_translation_stays_on_native_async_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["original"])],
            )])
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_head.xml")
            source._validate()
            translator = _AsyncXMLTranslator()
            target = await AsyncPDFCraft().translate_extraction(
                source,
                root / "target.pcex",
                cast(Any, translator),
                target_language="en",
            )
            self.assertEqual(translator.thread_id, threading.get_ident())
            with target._materialize() as paths:
                translation_id = (await AsyncPDFCraft().list_translations(target))[0].id
                self.assertIn(
                    "async:original",
                    (paths.translations / translation_id / "chapters/chapter_head.xml").read_text(
                        encoding="utf-8"
                    ),
                )

    async def test_internal_custom_async_transform_protocol_is_awaited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["original"])],
            )])
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_head.xml")
            source._validate()
            transformer = _AsyncChapterTransformer()
            target = await ChapterExtractionTransformer(transformer).transform(
                source, root / "target.pcex",
            )
            self.assertEqual(transformer.thread_id, threading.get_ident())
            with target._materialize() as paths:
                self.assertIn(
                    "native async extension",
                    (paths.chapters / "chapter_head.xml").read_text(encoding="utf-8"),
                )

    async def test_async_pdf_handler_protocol_is_awaited_on_caller_loop(self):
        loop_thread = threading.get_ident()
        async_document = _AsyncDocument()
        async_handler = _AsyncHandler(async_document)
        craft = AsyncPDFCraft(PDFOptions(pdf_handler=async_handler))
        bridge = craft._sync_pdf_handler()
        self.assertIsInstance(bridge, _AsyncPDFHandlerBridge)
        assert bridge is not None

        document = await asyncio.to_thread(bridge.open, Path("source.pdf"))
        self.assertEqual(await asyncio.to_thread(lambda: document.pages_count), 2)
        self.assertEqual(
            await asyncio.to_thread(document.page_size, 1), (8.5, 11.0),
        )
        await asyncio.to_thread(document.close)
        self.assertEqual(async_handler.thread_id, loop_thread)
        self.assertEqual(async_document.thread_ids, [loop_thread] * 3)
        self.assertTrue(async_document.closed)

    async def test_async_pdf_handler_works_through_patch_and_translate_pdf(self):
        loop_thread = threading.get_ident()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source_pdf = canvas.Canvas(str(source), pagesize=(100, 100))
            source_pdf.drawString(5, 50, "Original")
            source_pdf.save()
            extraction = make_extraction(
                root / "extraction", page_pixel_sizes={1: (100, 100)}, render_dpi=72,
            )
            async_document = _AsyncDocument()
            async_handler = _AsyncHandler(async_document)
            craft = AsyncPDFCraft(PDFOptions(pdf_handler=async_handler))

            await craft.patch_pdf_with_extraction(
                source, extraction, root / "patched.pdf", ignore_errors=True,
            )
            with patch.object(
                craft, "_translate_to_workspace",
                new_callable=AsyncMock, return_value=extraction,
            ) as translate:
                await craft.translate_pdf(
                    source, extraction, root / "translated.pdf",
                    cast(Any, _AsyncXMLTranslator()), ignore_errors=True,
                )

            translate.assert_awaited_once()
            self.assertTrue((root / "patched.pdf").is_file())
            self.assertTrue((root / "translated.pdf").is_file())
            self.assertEqual(async_handler.thread_id, loop_thread)
            self.assertEqual(async_handler.open_count, 2)
            self.assertTrue(async_document.closed)
            self.assertTrue(async_document.thread_ids)
            self.assertEqual(set(async_document.thread_ids), {loop_thread})

    async def test_patch_facades_accept_local_ignore_errors_checker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source_pdf = canvas.Canvas(str(source), pagesize=(100, 100))
            source_pdf.drawString(5, 50, "Original")
            source_pdf.save()
            extraction_root = root / "extraction"
            extraction = make_extraction(
                extraction_root, page_pixel_sizes={1: (100, 100)}, render_dpi=72,
            )
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (5, 40, 80, 60), ["Translated"])],
            )])
            save_xml(encode(chapter), extraction_root / "chapters" / "chapter_head.xml")
            (extraction_root / "translation.xml").write_text(
                "<translation><narrative><paragraph chapter_id='head' "
                "page_index='1' order='1' state='translated'/></narrative></translation>",
                encoding="utf-8",
            )
            extraction._validate()

            for mode in ("async", "sync"):
                with self.subTest(mode=mode):
                    target = root / f"{mode}.pdf"
                    checker = lambda _error: True  # noqa: E731
                    if mode == "async":
                        await AsyncPDFCraft().patch_pdf_with_extraction(
                            source, extraction, target, ignore_errors=checker,
                        )
                    else:
                        await asyncio.to_thread(
                            PDFCraft().patch_pdf_with_extraction,
                            source, extraction, target,
                            ignore_errors=checker,
                        )
                    self.assertTrue(target.is_file())
            self.assertFalse(any(
                thread.name == "pdf-craft-ignore-errors" and thread.is_alive()
                for thread in threading.enumerate()
            ))

    async def test_local_ignore_checker_receives_remote_page_error(self):
        loop_thread = threading.get_ident()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source_pdf = canvas.Canvas(str(source), pagesize=(100, 100))
            source_pdf.drawString(5, 50, "Original")
            source_pdf.save()
            extraction_root = root / "extraction"
            extraction = make_extraction(
                extraction_root,
                page_pixel_sizes={1: (100, 100), 2: (100, 100)},
                render_dpi=72,
            )
            chapter = Chapter(None, -1, [
                TextFlowItem(
                    "body", 0,
                    [SourceTextFragment(1, 1, (5, 40, 80, 60), ["First"])],
                ),
                TextFlowItem(
                    "body", 0,
                    [SourceTextFragment(2, 1, (5, 40, 80, 60), ["Second"])],
                ),
            ])
            save_xml(encode(chapter), extraction_root / "chapters" / "chapter_head.xml")
            (extraction_root / "translation.xml").write_text(
                "<translation><narrative>"
                "<paragraph chapter_id='head' page_index='1' order='1' state='translated'/>"
                "<paragraph chapter_id='head' page_index='2' order='1' state='translated'/>"
                "</narrative></translation>",
                encoding="utf-8",
            )
            extraction._validate()
            observed: list[tuple[Exception, int]] = []

            def checker(error: Exception) -> bool:
                observed.append((error, threading.get_ident()))
                return True

            with self.assertRaisesRegex(ValueError, "page_index 2"):
                await AsyncPDFCraft().patch_pdf_with_extraction(
                    source, extraction, root / "target.pdf", ignore_errors=checker,
                )

            self.assertEqual(len(observed), 1)
            self.assertIsInstance(observed[0][0], ValueError)
            self.assertEqual(observed[0][1], loop_thread)
            self.assertFalse(any(
                thread.name == "pdf-craft-ignore-errors" and thread.is_alive()
                for thread in threading.enumerate()
            ))

    async def test_async_subprocess_and_cancellation_cleanup(self):
        stdout, _ = await run_subprocess(
            sys.executable, "-c", "print('ready')",
        )
        self.assertEqual(stdout.strip(), b"ready")
        task = asyncio.create_task(run_subprocess(
            sys.executable, "-c", "import time; time.sleep(30)",
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_cancellation_reaches_cooperative_ocr_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _CancellableEngine()
            task = asyncio.create_task(
                AsyncPDFCraft.from_engine(engine).convert_pdf_to_markdown(
                    "source.pdf", Path(directory) / "book.md",
                )
            )
            await asyncio.to_thread(engine.started.wait, 1)
            started = asyncio.get_running_loop().time()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            elapsed = asyncio.get_running_loop().time() - started
            self.assertTrue(engine.observed_cancel.is_set())
            self.assertTrue(engine.finished.is_set())
            self.assertTrue(engine.late_write_succeeded)
            self.assertGreaterEqual(elapsed, 0.12)
            assert engine.analysing_path is not None
            self.assertFalse(engine.analysing_path.exists())


if __name__ == "__main__":
    unittest.main()
