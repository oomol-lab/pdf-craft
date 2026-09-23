# pylint: disable=protected-access
import asyncio
from datetime import datetime, timezone
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import (
    AsyncPDFCraft, ExtractionOptions, PDFCraft, PDFDocumentMetadata, PDFOptions,
    SubmitKind, translate_epub,
)
from pdf_craft.craft import _AsyncPDFHandlerBridge
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.llm import LLM, runtime_for
from pdf_craft.runtime import QT_DOMAIN, invoke_callback, run_subprocess
from pdf_craft.pipeline.pdf.text_layout import (
    _ensure_qt_application, _qt_lifecycle_probe, _qt_modules,
)
from pdf_craft.transformer import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator.concurrency import (
    run_concurrency_async,
)
from tests.extraction_helpers import make_extraction


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


class _AsyncXMLTranslator:
    def __init__(self) -> None:
        self.thread_id: int | None = None

    def translate_element(self, task, **_kwargs):
        return task.element, task.payload

    async def translate_element_async(self, task, **_kwargs):
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
            self.assertTrue(extraction.validate())
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

    async def test_llm_transport_is_awaitable_and_sync_adapter_is_guarded(self):
        config = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            retry_times=0,
        )
        runtime = runtime_for(config)

        async def invoke(*_args):
            await asyncio.sleep(0)
            return "translated"

        runtime._invoke = invoke  # type: ignore[method-assign]
        self.assertEqual(await runtime.request_async("hello", use_cache=False), "translated")
        with self.assertRaisesRegex(RuntimeError, "active event loop"):
            runtime.request("hello", use_cache=False)

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
            "pdf_craft.craft.run_epub_translation_async", new_callable=AsyncMock,
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
            "pdf_craft.pipeline.epub.translation.translator.translate_async",
            new_callable=AsyncMock,
        ) as translate:
            with self.assertRaisesRegex(RuntimeError, "active event loop"):
                translate_epub(
                    "source.epub",
                    "target.epub",
                    target_language="zh",
                    submit=SubmitKind.REPLACE,
                )
        translate.assert_called_once()
        translate.assert_not_awaited()

    async def test_public_sync_epub_entry_delegates_to_async_pipeline(self):
        with patch(
            "pdf_craft.pipeline.epub.translation.translator.translate_async",
            new_callable=AsyncMock,
        ) as translate:
            await asyncio.to_thread(
                translate_epub,
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
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_1.xml")
            source.validate()
            translator = _AsyncXMLTranslator()
            target = await AsyncPDFCraft().translate_extraction(
                source,
                root / "target.pcex",
                ChapterXMLTransformer(translator),
            )
            self.assertEqual(translator.thread_id, threading.get_ident())
            with target._materialize() as paths:
                self.assertIn(
                    "async:original",
                    (paths.chapters / "chapter_1.xml").read_text(encoding="utf-8"),
                )

    async def test_custom_async_transform_protocol_is_awaited(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0,
                [SourceTextFragment(1, 1, (1, 1, 5, 5), ["original"])],
            )])
            save_xml(encode(chapter), root / "source" / "chapters" / "chapter_1.xml")
            source.validate()
            transformer = _AsyncChapterTransformer()
            target = await AsyncPDFCraft().translate_extraction(
                source, root / "target.pcex", transformer,
            )
            self.assertEqual(transformer.thread_id, threading.get_ident())
            with target._materialize() as paths:
                self.assertIn(
                    "native async extension",
                    (paths.chapters / "chapter_1.xml").read_text(encoding="utf-8"),
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
                craft, "translate_extraction",
                new_callable=AsyncMock, return_value=extraction,
            ) as translate:
                await craft.translate_pdf(
                    source, extraction, root / "translated.pdf",
                    _AsyncChapterTransformer(), ignore_errors=True,
                )

            translate.assert_awaited_once()
            self.assertTrue((root / "patched.pdf").is_file())
            self.assertTrue((root / "translated.pdf").is_file())
            self.assertEqual(async_handler.thread_id, loop_thread)
            self.assertEqual(async_handler.open_count, 2)
            self.assertTrue(async_document.closed)
            self.assertTrue(async_document.thread_ids)
            self.assertEqual(set(async_document.thread_ids), {loop_thread})

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
