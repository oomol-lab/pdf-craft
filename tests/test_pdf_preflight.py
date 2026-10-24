"""Backend compatibility checks use real PDFs; no OCR or hosted LLM calls."""
# pylint: disable=protected-access
import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path
import pickle
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch
from typing import Any, cast

import pypdf
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject, RectangleObject

from pdf_craft import AsyncPDFCraft, PDFCraft, PDFPreflightError, PDFOptions, ExtractionOptions
from pdf_craft.concurrency import NonContinuableError
from pdf_craft.extractor import PDFExtractor
from pdf_craft.pdf.handler import DefaultPDFHandler
from pdf_craft.pdf.page_ref import PageRef
from pdf_craft.pdf.poppler import PopplerDocument
from pdf_craft.pdf.preflight import check_pdf, pdf_operation, _read_pypdf_page
from pdf_craft.pipeline.pdf import PDFPatcher, PDFTranslationPipeline


ORIGINAL = Path(__file__).parent / 'assets/pdf/xref-offset-997ad598.pdf'


def write_pdf(path, *, labelled=False, pages=2):
    writer = pypdf.PdfWriter()
    writer.add_metadata({'/Title': 'Preflight title', '/Author': 'One; Two'})
    for index in range(pages):
        page = writer.add_blank_page(200 + index * 40, 300 + index * 40)
        if index == 1:
            page.mediabox = RectangleObject((10, 20, 250, 360))
            page.cropbox = RectangleObject((20, 30, 230, 340))
            page[NameObject('/Rotate')] = NumberObject(90)
    if labelled:
        writer.set_page_label(0, pages - 1, cast(pypdf.constants.PageLabelStyle, pypdf.constants.PageLabelStyle.UPPERCASE_ROMAN))
    writer.write(path)
    writer.close()


class PDFPreflightTests(unittest.TestCase):
    def test_original_bytes_rejected_before_ocr_but_poppler_reads_251_pages(self):
        self.assertEqual(ORIGINAL.stat().st_size, 2233754)
        self.assertEqual(hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(),
                         'c32eb117290bbca606fada374101f86b741f345abd9720d1c9dea6972f525ea3')
        with self.assertRaises(PDFPreflightError) as caught:
            check_pdf(ORIGINAL)
        self.assertEqual(caught.exception.backend, 'pypdf')
        self.assertIsNotNone(caught.exception.__cause__)
        document = DefaultPDFHandler().open(ORIGINAL)
        try:
            self.assertEqual(document.pages_count, 251)
            for number in (1, 251):
                image = document.render_page(number, 36)
                try:
                    self.assertGreater(image.width, 0)
                finally:
                    image.close()
        finally:
            document.close()

    def test_native_render_error_with_exit_zero_stops_all_business_work(self):
        from pdf_craft.runtime import run_subprocess_sync
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'unknown-xobject.pdf'
            writer = pypdf.PdfWriter()
            writer.add_blank_page(200, 300)
            page = writer.add_blank_page(200, 300)
            content = DecodedStreamObject()
            content.set_data(b'q /MissingXObject Do Q')
            page[NameObject('/Contents')] = writer._add_object(content)
            writer.write(source)
            writer.close()
            original = source.read_bytes()
            # This is a real subprocess, not broken_render injection: Poppler
            # returns success and a readable PPM even though the image is wrong.
            output, stderr = run_subprocess_sync(
                'pdftoppm', '-f', '2', '-l', '2', '-singlefile', '-r', '36', str(source),
            )
            self.assertTrue(output.startswith(b'P6'))
            self.assertIn(b"Syntax Error: XObject 'MissingXObject' is unknown", stderr)
            with self.assertRaises(PDFPreflightError) as caught:
                check_pdf(source)
            error = caught.exception
            self.assertEqual((error.backend, error.stage, error.page_index), ('poppler', 'render', 2))
            self.assertIn("MissingXObject", error.reason)
            self.assertIn('Syntax Error', str(error.__cause__))
            business = Mock()
            @pdf_operation
            async def operation(source):
                business(source)
            with self.assertRaises(PDFPreflightError):
                asyncio.run(operation(source))
            business.assert_not_called()
            engine = Mock()
            engine.extract_package_async = AsyncMock()
            notify = Mock()
            decide = Mock(return_value=True)
            options = ExtractionOptions(on_body=notify, on_footnote=notify,
                                        on_metadata=notify, on_ocr_event=notify)
            for policy in (True, decide):
                with self.subTest(policy=policy), self.assertRaises(PDFPreflightError) as caught:
                    asyncio.run(AsyncPDFCraft.from_engine(engine).extract_pdf(
                        source, root / 'book.pcex', options, error_policy=policy,
                    ))
                self.assertEqual((caught.exception.backend, caught.exception.stage, caught.exception.page_index),
                                 ('poppler', 'render', 2))
            engine.extract_package_async.assert_not_called()
            notify.assert_not_called()
            decide.assert_not_called()
            self.assertFalse((root / 'book.pcex').exists())
            self.assertEqual(source.read_bytes(), original)

    def test_native_syntax_warning_does_not_reject_renderable_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'warning.pdf'
            writer = pypdf.PdfWriter()
            page = writer.add_blank_page(200, 300)
            content = DecodedStreamObject()
            # A stray marked-content end is ignored with a Syntax Warning;
            # the visible rectangle still renders normally.
            content.set_data(b'EMC q 0 0 10 10 re f Q')
            page[NameObject('/Contents')] = writer._add_object(content)
            writer.write(source)
            writer.close()
            with self.assertLogs('pdf_craft.pdf.poppler', level='WARNING') as logs:
                result = check_pdf(source)
            self.assertEqual(len(result.pages), 1)
            self.assertTrue(any('Syntax Warning' in line and 'EMC' in line for line in logs.output))

    def test_poppler_error_categories_are_fatal_but_warnings_remain_visible(self):
        document = PopplerDocument(Path('unused.pdf'))
        for category in ('Syntax Error', 'Config Error', 'Command Line Error', 'I/O Error',
                         'Permission Error', 'Unimplemented Feature', 'Internal Error', 'Error'):
            for position in ('', ' (123)'):
                diagnostic = f'{category}{position}: failure\n'.encode()
                with self.subTest(category=category, position=position), patch(
                    'pdf_craft.pdf.poppler.run_subprocess_sync', return_value=(b'output', diagnostic),
                ), self.assertRaisesRegex(RuntimeError, 'failure'):
                    document._run('pdftoppm')
        diagnostic = b'Syntax Warning (123): unusual but readable content\n'
        with patch('pdf_craft.pdf.poppler.run_subprocess_sync', return_value=(b'output', diagnostic)), \
                self.assertLogs('pdf_craft.pdf.poppler', level='WARNING') as logs:
            self.assertEqual(document._run('pdftoppm'), b'output')
        self.assertIn(diagnostic.decode().strip(), logs.output[0])

    def test_valid_geometry_metadata_labels_and_default_handler_use_poppler(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'valid.pdf'
            write_pdf(path, labelled=True)
            before = path.read_bytes()
            result = check_pdf(path)
            self.assertEqual(result.labels, {1: 'I', 2: 'II'})
            self.assertEqual(result.pages[2].rotation, 90)
            self.assertEqual(result.pages[2].media_box, (10, 20, 250, 360))
            self.assertEqual(result.pages[2].crop_box, (20, 30, 230, 340))
            with patch('pypdf.PdfReader', side_effect=AssertionError('read-only adapter must use Poppler')):
                document = DefaultPDFHandler().open(path)
                try:
                    self.assertEqual(document.pages_count, 2)
                    self.assertEqual(document.metadata().title, 'Preflight title')
                    self.assertEqual(document.metadata().authors, ['One', 'Two'])
                    self.assertEqual(document.page_size(2), (340 / 72, 240 / 72))
                    image = PageRef(document, 2).render(72, max_image_file_size=1000000)
                    try:
                        self.assertEqual(image.size, (340, 240))
                    finally:
                        image.close()
                finally:
                    document.close()
            self.assertEqual(path.read_bytes(), before)
            write_pdf(path, labelled=False)
            self.assertEqual(check_pdf(path).labels, {})

    def test_original_rejected_by_all_facades_and_advanced_components_without_callbacks(self):
        callback = Mock(return_value=True)
        engine = Mock()
        engine.extract_package_async = AsyncMock()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output.pcex'
            craft = AsyncPDFCraft.from_engine(engine)
            operations = [
                lambda: craft.extract_pdf(ORIGINAL, output, error_policy=callback),
                lambda: craft.extract_pdf_with_metering(ORIGINAL, output, error_policy=True),
                lambda: craft.convert_pdf_to_markdown(ORIGINAL, output, error_policy=True),
                lambda: craft.convert_pdf_to_epub(ORIGINAL, output, error_policy=callback),
                lambda: craft.patch_pdf_with_extraction(ORIGINAL, output, output, error_policy=True),
                lambda: craft.translate_pdf(ORIGINAL, output, output, cast(Any, object()), error_policy=callback),
                lambda: PDFExtractor(engine).extract(ORIGINAL, output, error_policy=True),
                lambda: PDFTranslationPipeline().patch(ORIGINAL, output, output, error_policy=True),
                lambda: PDFPatcher().patch(ORIGINAL, output, [], error_policy=callback),
            ]
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(PDFPreflightError):
                    asyncio.run(operation())
            with self.assertRaises(PDFPreflightError):
                PDFCraft.from_engine(engine).extract_pdf(ORIGINAL, output, error_policy=True)
            engine.extract_package_async.assert_not_called()
            callback.assert_not_called()
            self.assertFalse(output.exists())

    def test_effective_crop_intersection_and_all_rotations_agree(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            writer = pypdf.PdfWriter()
            for rotation in (0, 90, 180, 270):
                page = writer.add_blank_page(200, 300)
                page.cropbox = RectangleObject((-10, -20, 250, 350))
                page.rotate(rotation)
            writer.write(source)
            writer.close()
            result = check_pdf(source)
            self.assertEqual(len(result.pages), 4)
            for index, rotation in enumerate((0, 90, 180, 270), 1):
                self.assertEqual(result.pages[index].crop_box, (0, 0, 200, 300))
                self.assertEqual(result.pages[index].rotation, rotation)

    def test_second_page_failures_keep_backend_stage_and_location(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'valid.pdf'
            write_pdf(path)
            native_read = _read_pypdf_page
            native_render = PopplerDocument.render_page
            native_geometry = PopplerDocument.geometry
            def broken_content(reader, index):
                if index == 2:
                    raise ValueError('bad page content stream')
                return native_read(reader, index)
            def broken_render(document, index, dpi):
                if index == 2:
                    raise ValueError('cannot rasterize')
                return native_render(document, index, dpi)
            def mismatched(document, index):
                geometry = native_geometry(document, index)
                return replace(geometry, rotation=0) if index == 2 else geometry
            for target, implementation, backend, stage in (
                ('pdf_craft.pdf.preflight._read_pypdf_page', broken_content, 'pypdf', 'page_read'),
                ('pdf_craft.pdf.poppler.PopplerDocument.render_page', broken_render, 'poppler', 'render'),
                ('pdf_craft.pdf.poppler.PopplerDocument.geometry', mismatched, 'consistency', 'geometry'),
            ):
                with self.subTest(stage=stage), patch(target, implementation):
                    with self.assertRaises(PDFPreflightError) as caught:
                        check_pdf(path)
                    error = caught.exception
                    self.assertEqual((error.backend, error.stage, error.page_index), (backend, stage, 2))
                    self.assertIsNotNone(error.__cause__)

    def test_trial_write_and_reopen_failures_are_reported_and_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'valid.pdf'
            write_pdf(path, pages=1)
            original = pypdf.PdfReader
            def reader(source, **kwargs):
                if Path(source).name == 'trial.pdf':
                    raise ValueError('cannot reopen trial')
                return original(source, **kwargs)
            for target, replacement, stage in (
                ('pypdf.PdfWriter.write', Mock(side_effect=OSError('disk full')), 'write'),
                ('pypdf.PdfReader', reader, 'reopen'),
            ):
                with self.subTest(stage=stage), patch(target, replacement):
                    with self.assertRaises(PDFPreflightError) as caught:
                        check_pdf(path)
                    self.assertEqual(caught.exception.stage, stage)
                    self.assertEqual(caught.exception.backend, 'pypdf')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_missing_input_and_tool_have_distinct_causes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.pdf'
            with self.assertRaises(PDFPreflightError) as caught:
                check_pdf(path)
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
            write_pdf(path, pages=1)
            with patch('pdf_craft.pdf.poppler.run_subprocess_sync', side_effect=FileNotFoundError('pdfinfo unavailable')):
                with self.assertRaises(PDFPreflightError) as caught:
                    check_pdf(path)
                self.assertEqual((caught.exception.backend, caught.exception.stage), ('poppler', 'open'))

    def test_nested_operations_share_only_current_task_results(self):
        calls = []
        @pdf_operation
        async def inner(source):
            calls.append(source)
        @pdf_operation
        async def outer(source):
            await inner(source)
            await inner(source)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source, pages=1)
            native = PopplerDocument.render_page
            with patch.object(PopplerDocument, 'render_page', autospec=True, side_effect=native) as rendering:
                asyncio.run(outer(source))
                self.assertEqual(rendering.call_count, 1)
                asyncio.run(outer(source))
                self.assertEqual(rendering.call_count, 2)
        self.assertEqual(len(calls), 4)

    def test_cancellation_before_preflight_does_not_turn_into_input_error(self):
        with self.assertRaises(asyncio.CancelledError):
            check_pdf(ORIGINAL, lambda: True)

    def test_cancelled_render_reaps_subprocess_and_releases_task_scope(self):
        async def exercise(source, marker):
            @pdf_operation
            async def operation(source):
                raise AssertionError("must not enter business work")
            def blocking_render(document, _index, _dpi):
                script = "import os,time,pathlib;pathlib.Path(" + repr(str(marker)) + ").write_text(str(os.getpid()));time.sleep(60)"
                document._run(sys.executable, '-c', script)
                raise AssertionError("worker should be cancelled")
            with patch.object(PopplerDocument, 'render_page', blocking_render):
                task = asyncio.create_task(operation(source))
                try:
                    for _ in range(200):
                        if marker.exists():
                            break
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                    self.assertTrue(marker.exists())
                    pid = int(marker.read_text())
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                finally:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
            # A cancelled check cannot be reused as a successful one.
            check_pdf(source)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'valid.pdf'
            write_pdf(source, pages=1)
            asyncio.run(exercise(source, Path(folder) / 'child.pid'))

    def test_callbacks_and_custom_handler_are_untouched_when_preflight_fails(self):
        handler = Mock()
        handler.open = AsyncMock()
        notify = Mock()
        craft = AsyncPDFCraft(pdf=PDFOptions(pdf_handler=handler))
        options = ExtractionOptions(on_body=notify, on_footnote=notify,
                                    on_metadata=notify, on_ocr_event=notify)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(PDFPreflightError):
            asyncio.run(craft.extract_pdf(ORIGINAL, Path(folder) / 'book.pcex', options, error_policy=True))
        handler.open.assert_not_called()
        notify.assert_not_called()

    def test_valid_extraction_composition_runs_preflight_once(self):
        from tests.extraction_helpers import make_extraction
        class Engine:
            async def extract_package_async(self, *, analysing_path, **_kwargs):
                make_extraction(analysing_path / 'extraction', page_pixel_sizes={1: (200, 300)})
                return None, None, None, None, 'metering'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'valid.pdf'
            write_pdf(source, pages=1)
            native = PopplerDocument.render_page
            with patch.object(PopplerDocument, 'render_page', autospec=True, side_effect=native) as rendering:
                result = asyncio.run(AsyncPDFCraft.from_engine(Engine()).convert_pdf_to_markdown(source, root / 'book.md'))
            self.assertEqual(result, 'metering')
            self.assertTrue((root / 'book.md').is_file())
            self.assertEqual(rendering.call_count, 1)

    def test_source_changes_are_not_cached_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source, pages=1)
            native = PopplerDocument.render_page
            def mutate(document, index, dpi):
                image = native(document, index, dpi)
                with source.open('ab') as stream:
                    stream.write(b'\n% changed during check\n')
                return image
            with patch.object(PopplerDocument, 'render_page', mutate):
                with self.assertRaises(PDFPreflightError) as caught:
                    check_pdf(source)
            self.assertEqual(caught.exception.stage, 'source_changed')

    def test_backend_page_count_disagreement_fails_before_any_raster(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source, pages=1)
            with patch.object(PopplerDocument, 'info', new_callable=PropertyMock,
                              return_value={'Pages': '2'}), patch.object(PopplerDocument, 'render_page') as render:
                with self.assertRaises(PDFPreflightError) as caught:
                    check_pdf(source)
                self.assertEqual((caught.exception.backend, caught.exception.stage), ('consistency', 'page_count'))
                render.assert_not_called()

    def test_exception_survives_real_process_execution_domain(self):
        from pdf_craft.runtime import QT_DOMAIN
        with self.assertRaises(PDFPreflightError) as caught:
            asyncio.run(QT_DOMAIN.run(check_pdf, ORIGINAL))
        self.assertEqual(caught.exception.source, str(ORIGINAL))
        self.assertEqual(caught.exception.backend, 'pypdf')
        self.assertIsNotNone(caught.exception.__cause__)

    def test_exception_pickle_preserves_public_payload_and_fatal_identity(self):
        error = PDFPreflightError('source.pdf', 'poppler', 'render', 12, 'failed')
        error.__cause__ = ValueError('native cause')
        restored = pickle.loads(pickle.dumps(error))
        self.assertIsInstance(restored, NonContinuableError)
        self.assertEqual((restored.source, restored.backend, restored.stage, restored.page_index, restored.reason),
                         ('source.pdf', 'poppler', 'render', 12, 'failed'))
        self.assertEqual(str(restored.__cause__), 'native cause')
