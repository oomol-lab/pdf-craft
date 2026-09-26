"""Facade-level lifecycle coverage for external PDF tool processes."""

# pylint: disable=protected-access

import asyncio
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft import AsyncPDFCraft, PDFDocumentMetadata, PDFOptions
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.pdf.furniture import extract_furnitures
from pdf_craft.runtime import run_subprocess, run_subprocess_sync
from tests.extraction_helpers import make_extraction


_FAKE_TOOL = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

pid_path = Path(os.environ["PDF_CRAFT_TEST_PID_PATH"])
mode = os.environ["PDF_CRAFT_TEST_TOOL_MODE"]
pids = [os.getpid()]
if mode in {"sleep", "success", "fail"}:
    ready_path = pid_path.with_suffix(".child-ready")
    child = subprocess.Popen([
        sys.executable, "-c",
        "import signal,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text('ready'); time.sleep(60)",
        str(ready_path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while not ready_path.exists():
        time.sleep(0.01)
    pids.append(child.pid)
pid_path.write_text(",".join(map(str, pids)), encoding="utf-8")
if mode == "sleep":
    time.sleep(60)
if mode == "fail":
    sys.stderr.write("intentional failure")
    raise SystemExit(9)
if Path(sys.argv[0]).name == "pdftotext":
    sys.stdout.write("<doc><page width='72' height='72'><line xMin='1' yMin='2' xMax='20' yMax='8'><word xMin='1' yMin='2' xMax='20' yMax='8'>Header</word></line></page></doc>")
else:
    output = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("-sOutputFile="))
    shutil.copyfile(sys.argv[-1], output)
'''


class _FurnitureEngine:
    async def prepare_extract(self, *, pdf_path, **_kwargs):
        stdout, _ = await run_subprocess(
            "pdftotext", "-bbox-layout", str(pdf_path), "-",
        )
        return {
            "native_pdf_text": stdout,
            "native_pdf_text_prepared": True,
        }

    def extract_package(
        self,
        *,
        pdf_path,
        analysing_path,
        aborted,
        native_pdf_text,
        native_pdf_text_prepared,
        **_kwargs,
    ):
        ocr = analysing_path / "ocr"
        ocr.mkdir(parents=True)
        (ocr / "page_1.xml").write_text("<page/>", encoding="utf-8")
        (ocr / "page_pixel_sizes.json").write_text(
            '{"1": [100, 100]}', encoding="utf-8",
        )
        extract_furnitures(
            pdf_path,
            ocr,
            aborted=aborted,
            native_pdf_text=native_pdf_text,
            native_pdf_text_prepared=native_pdf_text_prepared,
        )
        raise AssertionError("sleeping pdftotext unexpectedly returned")


class _AsyncPatchDocument:
    async def pages_count(self) -> int:
        return 1

    async def metadata(self) -> PDFDocumentMetadata:
        return PDFDocumentMetadata(
            title=None, description=None, publisher=None, isbn=None,
            authors=[], editors=[], translators=[],
            modified=datetime.now(timezone.utc),
        )

    async def page_size(self, page_index: int) -> tuple[float, float]:
        assert page_index == 1
        return 100 / 72, 100 / 72

    async def render_page(self, page_index: int, dpi: int) -> Image.Image:
        assert (page_index, dpi) == (1, 72)
        return Image.new("RGB", (100, 100), "white")

    async def close(self) -> None:
        return None


class _AsyncPatchHandler:
    async def open(self, pdf_path: Path) -> _AsyncPatchDocument:
        del pdf_path
        return _AsyncPatchDocument()


class TestSubprocessLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_direct_commands_reap_children_after_success_and_failure(self):
        runners = (
            run_subprocess,
            _run_subprocess_sync_async,
        )
        for runner in runners:
            for mode in ("success", "fail"):
                with self.subTest(runner=runner, mode=mode), tempfile.TemporaryDirectory() as directory:
                    pid_path = Path(directory) / "pid"
                    operation = runner(*_tree_command(pid_path, mode))
                    if mode == "success":
                        await operation
                    else:
                        with self.assertRaisesRegex(RuntimeError, "exit code 9"):
                            await operation
                    await _assert_processes_dead(
                        self, await _wait_for_pids(pid_path),
                    )

    async def test_direct_async_command_cancellation_reaps_children(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "pid"
            task = asyncio.create_task(run_subprocess(
                *_tree_command(pid_path, "sleep"),
            ))
            pids = await _wait_for_pids(pid_path)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            await _assert_processes_dead(self, pids)

    @unittest.skipIf(os.name == "nt", "executable PATH fixture is POSIX-only")
    async def test_pdftotext_success_and_failure_are_reaped(self):
        for mode in ("success", "fail"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                executable = _write_fake_tool(root, "pdftotext")
                del executable
                ocr = root / "ocr"
                ocr.mkdir()
                (ocr / "page_1.xml").write_text("<page/>", encoding="utf-8")
                (ocr / "page_pixel_sizes.json").write_text(
                    '{"1": [100, 100]}', encoding="utf-8",
                )
                pid_path = root / "pid"
                with _tool_environment(root, pid_path, mode):
                    result = await asyncio.to_thread(
                        extract_furnitures, root / "source.pdf", ocr,
                    )
                pids = await _wait_for_pids(pid_path)
                await _assert_processes_dead(self, pids)
                sections = result.findall("pages/page/section")
                self.assertEqual(
                    [section.text for section in sections],
                    ["Header"] if mode == "success" else [],
                )

    @unittest.skipIf(os.name == "nt", "executable PATH fixture is POSIX-only")
    async def test_extract_cancellation_reaps_pdftotext_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_tool(root, "pdftotext")
            pid_path = root / "pid"
            with _tool_environment(root, pid_path, "sleep"):
                task = asyncio.create_task(
                    AsyncPDFCraft.from_engine(_FurnitureEngine()).extract_pdf(
                        root / "source.pdf", root / "output.pcex",
                    )
                )
                pids = await _wait_for_pids(pid_path)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            await _assert_processes_dead(self, pids)

    @unittest.skipIf(os.name == "nt", "executable PATH fixture is POSIX-only")
    async def test_qt_patch_success_and_failure_reap_ghostscript(self):
        for mode in ("success", "fail"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_fake_tool(root, "gs")
                source, extraction = _patch_fixture(root)
                pid_path = root / "pid"
                craft = AsyncPDFCraft(PDFOptions(pdf_handler=_AsyncPatchHandler()))
                with _tool_environment(root, pid_path, mode):
                    operation = craft.patch_pdf_with_extraction(
                        source, extraction, root / "target.pdf",
                    )
                    if mode == "success":
                        await operation
                        self.assertTrue((root / "target.pdf").is_file())
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Ghostscript"):
                            await operation
                pids = await _wait_for_pids(pid_path)
                await _assert_processes_dead(self, pids)

    @unittest.skipIf(os.name == "nt", "executable PATH fixture is POSIX-only")
    async def test_qt_patch_cancellation_reaps_ghostscript_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_tool(root, "gs")
            source, extraction = _patch_fixture(root)
            pid_path = root / "pid"
            craft = AsyncPDFCraft(PDFOptions(pdf_handler=_AsyncPatchHandler()))
            with _tool_environment(root, pid_path, "sleep"):
                task = asyncio.create_task(
                    craft.patch_pdf_with_extraction(
                        source, extraction, root / "target.pdf",
                        ignore_errors=lambda _error: True,
                    )
                )
                pids = await _wait_for_pids(pid_path)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            await _assert_processes_dead(self, pids)
            self.assertFalse(any(
                thread.name == "pdf-craft-ignore-errors" and thread.is_alive()
                for thread in threading.enumerate()
            ))


def _write_fake_tool(root: Path, name: str) -> Path:
    executable = root / name
    executable.write_text(_FAKE_TOOL, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def _tree_command(pid_path: Path, mode: str) -> tuple[str, ...]:
    script = (
        "from pathlib import Path; import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "Path(sys.argv[1]).write_text(f'{os.getpid()},{child.pid}'); "
        "mode=sys.argv[2]; "
        "time.sleep(60) if mode == 'sleep' else None; "
        "raise SystemExit(9 if mode == 'fail' else 0)"
    )
    return sys.executable, "-c", f"import os; {script}", str(pid_path), mode


async def _run_subprocess_sync_async(*command: str) -> tuple[bytes, bytes]:
    return await asyncio.to_thread(run_subprocess_sync, *command)


def _tool_environment(root: Path, pid_path: Path, mode: str):
    return patch.dict(os.environ, {
        "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
        "PDF_CRAFT_TEST_PID_PATH": str(pid_path),
        "PDF_CRAFT_TEST_TOOL_MODE": mode,
    })


async def _wait_for_pids(path: Path) -> tuple[int, ...]:
    for _ in range(250):
        if path.exists() and (content := path.read_text(encoding="utf-8").strip()):
            return tuple(int(value) for value in content.split(","))
        await asyncio.sleep(0.02)
    raise AssertionError(f"external command did not write its PID file: {path}")


async def _assert_processes_dead(
    test: unittest.TestCase, pids: tuple[int, ...],
) -> None:
    for _ in range(250):
        if not any(_process_exists(pid) for pid in pids):
            return
        await asyncio.sleep(0.02)
    test.fail(f"external processes survived operation completion: {pids}")


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"),
            capture_output=True,
            check=False,
            text=True,
        )
        return any(
            len(row) > 1 and row[1] == str(pid)
            for row in csv.reader(result.stdout.splitlines())
        )
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _patch_fixture(root: Path):
    source = root / "source.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(100, 100))
    pdf.drawString(5, 50, "Original")
    pdf.save()
    extraction_root = root / "extraction"
    extraction = make_extraction(
        extraction_root, page_pixel_sizes={1: (100, 100)}, render_dpi=72,
    )
    chapter = Chapter(None, -1, [TextFlowItem(
        "body", 0,
        [SourceTextFragment(1, 1, (5, 40, 80, 60), ["Translated text"])],
    )])
    save_xml(encode(chapter), extraction_root / "chapters" / "chapter_head.xml")
    (extraction_root / "translation.xml").write_text(
        "<translation><narrative><paragraph chapter_id='head' page_index='1' "
        "order='1' state='translated'/></narrative></translation>",
        encoding="utf-8",
    )
    return source, extraction._validate()
