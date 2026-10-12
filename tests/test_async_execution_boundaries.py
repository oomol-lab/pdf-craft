"""Regression coverage for async orchestration and native worker ownership."""
# pylint: disable=protected-access

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
import unittest
from unittest.mock import patch
from typing import Any, cast

import pypdf
from PIL import Image

from pdf_craft.error import NoUsableFillPagesError
from pdf_craft.extractor.toc import analysing
from pdf_craft.pdf import PDFDocumentMetadata
from pdf_craft.pipeline.pdf import PDFPatcher, PDFReplacement, PatchTextOptions
from pdf_craft.runtime import worker_context


class TestAsyncExecutionBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_filesystem_context_entry_cleans_up_on_worker(self):
        loop = asyncio.get_running_loop()
        main_thread = get_ident()
        started = asyncio.Event()
        release = Event()
        cleaned = []

        @contextmanager
        def resource():
            loop.call_soon_threadsafe(started.set)
            release.wait(timeout=5)
            try:
                yield "resource"
            finally:
                cleaned.append(get_ident())

        async def use():
            async with worker_context(resource()):
                self.fail("cancelled entry must not reach the body")

        task = asyncio.create_task(use())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(cleaned), 1)
        self.assertNotEqual(cleaned[0], main_thread)

    async def test_toc_scan_cache_read_and_save_stay_off_event_loop(self):
        main_thread = get_ident()
        calls = []

        def off_loop(function):
            def invoke(*args, **kwargs):
                self.assertNotEqual(get_ident(), main_thread)
                calls.append(function.__name__)
                return function(*args, **kwargs)
            return invoke

        with TemporaryDirectory() as directory:
            pages = Path(directory) / "ocr"
            pages.mkdir()
            toc = Path(directory) / "toc.xml"
            with patch.object(analysing, "find_toc_pages", off_loop(analysing.find_toc_pages)), \
                 patch.object(analysing, "save_xml", off_loop(analysing.save_xml)), \
                 patch.object(analysing, "read_xml", off_loop(analysing.read_xml)):
                first = await analysing.analyse_toc(pages, toc, True)
                cached = await analysing.analyse_toc(pages, toc, True)
            self.assertEqual(first, cached)
            self.assertEqual(calls, ["find_toc_pages", "save_xml", "read_xml"])

    async def test_cancel_compiler_cleans_workspace_without_starting_qt(self):
        started = asyncio.Event()
        paths = []

        class Compiler:
            async def compile(self, source_path, target_path):
                paths.extend((source_path, target_path))
                started.set()
                await asyncio.Future()

        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            _blank_pdf(source)
            patcher = PDFPatcher(visual_base_compiler=Compiler())
            with patch("pdf_craft.pipeline.pdf.patcher.QT_DOMAIN.run") as native:
                task = asyncio.create_task(patcher.patch(
                    source, Path(directory) / "target.pdf", [_replacement()],
                ))
                await asyncio.wait_for(started.wait(), timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                native.assert_not_called()
            self.assertFalse(paths[0].parent.exists())

    async def test_async_pdf_extensions_stay_on_caller_loop_with_real_qt_worker(self):
        caller_loop = asyncio.get_running_loop()
        calls = []

        class Document:
            async def pages_count(self):
                return 1

            async def metadata(self):
                return PDFDocumentMetadata(None, None, None, None, [], [], [], datetime.now(timezone.utc))

            async def page_size(self, page_index):
                del page_index
                return 200 / 72, 100 / 72

            async def render_page(self, page_index, dpi):
                del page_index, dpi
                self_check()
                calls.append("render")
                return Image.new("RGB", (200, 100), "white")

            async def close(self):
                self_check()
                calls.append("close")

        def self_check():
            self.assertIs(asyncio.get_running_loop(), caller_loop)

        class Handler:
            async def open(self, pdf_path):
                del pdf_path
                self_check()
                calls.append("open")
                return Document()

        class Compiler:
            async def compile(self, source_path, target_path):
                del source_path
                self_check()
                calls.append("compile")
                await asyncio.to_thread(_blank_pdf, target_path)

        with TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.pdf", Path(directory) / "target.pdf"
            _blank_pdf(source)
            patcher = PDFPatcher(
                pdf_handler=Handler(), visual_base_compiler=Compiler(),
                options=PatchTextOptions(max_font_size=8, min_font_size=8),
            )
            await patcher.patch(source, target, [_replacement()])
            self.assertIn("translated", cast(Any, pypdf.PdfReader(target).pages[0]).extract_text())  # pylint: disable=no-member
            self.assertTrue(patcher.font_resolutions)
            self.assertEqual(calls, ["compile", "open", "render", "close"])

    async def test_low_level_recovery_callback_runs_on_caller_loop(self):
        caller_loop = asyncio.get_running_loop()
        errors = []

        def ignore(error):
            self.assertIs(asyncio.get_running_loop(), caller_loop)
            errors.append(error)
            return True

        class Compiler:
            async def compile(self, source_path, target_path):
                del source_path
                await asyncio.to_thread(_blank_pdf, target_path)

        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            _blank_pdf(source)
            patcher = PDFPatcher(visual_base_compiler=Compiler())
            with self.assertRaises(NoUsableFillPagesError):
                await patcher.patch(
                    source, Path(directory) / "target.pdf",
                    [PDFReplacement(1, (-10, 10, 180, 50), "invalid", (200, 100), 72)],
                    ignore_errors=ignore,
                )
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], ValueError)

    async def test_cancel_native_patch_also_cancels_pending_async_recovery_callback(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def ignore(_error):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
            return True

        class Compiler:
            async def compile(self, source_path, target_path):
                del source_path
                await asyncio.to_thread(_blank_pdf, target_path)

        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            _blank_pdf(source)
            patcher = PDFPatcher(visual_base_compiler=Compiler())
            task = asyncio.create_task(patcher.patch(
                source, Path(directory) / "target.pdf",
                [PDFReplacement(1, (-10, 10, 180, 50), "invalid", (200, 100), 72)],
                ignore_errors=cast(Any, ignore),
            ))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
            finally:
                task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            await asyncio.wait_for(stopped.wait(), timeout=5)


def _blank_pdf(path):
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 100)
    with path.open("wb") as output:
        writer.write(output)


def _replacement():
    return PDFReplacement(1, (10, 10, 180, 50), "translated", (200, 100), 72)
