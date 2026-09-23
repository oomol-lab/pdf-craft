# pylint: disable=protected-access
import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pdf_craft import AsyncPDFCraft, ExtractionOptions, PDFCraft, SubmitKind
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.llm import LLM, runtime_for
from pdf_craft.runtime import QT_DOMAIN, invoke_callback, run_subprocess
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

    def extract_package(self, *, aborted, **_kwargs):
        self.started.set()
        while not aborted():
            self.observed_cancel.wait(0.01)
        self.observed_cancel.set()
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
        first = await QT_DOMAIN.run(threading.get_ident)
        second = await QT_DOMAIN.run(threading.get_ident)
        self.assertEqual(first, second)

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
                AsyncPDFCraft.from_engine(engine).extract_pdf(
                    "source.pdf", Path(directory) / "book.pcex",
                )
            )
            await asyncio.to_thread(engine.started.wait, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            observed = await asyncio.to_thread(engine.observed_cancel.wait, 1)
            self.assertTrue(observed)


if __name__ == "__main__":
    unittest.main()
