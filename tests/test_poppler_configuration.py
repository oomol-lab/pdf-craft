"""Configured Poppler binaries must be used by both preflight and readers."""
# pylint: disable=protected-access
import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from typing import Any, cast
from unittest.mock import Mock, patch

from pdf_craft import AsyncPDFCraft, PDFPreflightError, pdf_pages_count
from pdf_craft.pdf.handler import DefaultPDFHandler
from pdf_craft.pdf.poppler import PopplerDocument
from pdf_craft.pdf.preflight import (check_pdf, current_poppler_path, pdf_operation,
                                     poppler_path_for)
from pdf_craft.pipeline.pdf import PDFPatcher, PDFTranslationPipeline, PDFReplacement
from tests.test_pdf_preflight import write_pdf


class PopplerConfigurationTests(unittest.TestCase):
    def test_explicit_directory_works_without_path_and_bad_directory_still_fails(self):
        executable = shutil.which('pdfinfo')
        assert executable is not None
        tools = Path(executable).parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.pdf'
            write_pdf(source, pages=1)
            with patch.dict(os.environ, {'PATH': folder}):
                configured = DefaultPDFHandler(poppler_path=tools)
                self.assertEqual(pdf_pages_count(source, configured), 1)
                self.assertEqual(check_pdf(source, poppler_path=tools).pages[1].size, (200, 300))
                document = configured.open(source)
                try:
                    image = document.render_page(1, 36)
                    image.close()
                finally:
                    document.close()
                for handler in (DefaultPDFHandler(), DefaultPDFHandler(root / 'not-installed')):
                    with self.subTest(handler=handler), self.assertRaises(PDFPreflightError) as caught:
                        pdf_pages_count(source, handler)
                    self.assertEqual((caught.exception.backend, caught.exception.stage), ('poppler', 'open'))
                # A task cannot silently fall back to PATH when its explicit path is wrong.
            with self.assertRaises(PDFPreflightError):
                pdf_pages_count(source, DefaultPDFHandler(root / 'not-installed'))

    def test_nested_checks_share_config_but_cannot_reuse_success_for_another_directory(self):
        executable = shutil.which('pdfinfo')
        assert executable is not None
        tools = Path(executable).parent
        class Operation:
            def __init__(self, directory):
                self.handler = DefaultPDFHandler(directory)
                self.calls = 0

            def _preflight_poppler_path(self):
                return poppler_path_for(self.handler)

            @pdf_operation
            async def inner(self, source):
                self.calls += 1
                # A default reader nested inside this operation uses the same installation.
                document = DefaultPDFHandler().open(source)
                try:
                    return document.pages_count
                finally:
                    document.close()

            @pdf_operation
            async def outer(self, source, other):
                await self.inner(source)
                await self.inner(source)
                with self_test.assertRaises(PDFPreflightError):
                    await other.inner(source)
        self_test = self
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source, pages=1)
            configured, invalid = Operation(tools), Operation(Path(folder) / 'missing')
            native = PopplerDocument.render_page
            with patch.dict(os.environ, {'PATH': folder}), patch.object(
                PopplerDocument, 'render_page', autospec=True, side_effect=native,
            ) as render:
                asyncio.run(configured.outer(source, invalid))
                self.assertEqual(render.call_count, 1)
                asyncio.run(configured.inner(source))
                self.assertEqual(render.call_count, 2)
            self.assertEqual(configured.calls, 3)
            self.assertEqual(invalid.calls, 0)
            self.assertIsNone(current_poppler_path())

    def test_advanced_patcher_and_pipeline_use_native_configuration(self):
        executable = shutil.which('pdfinfo')
        assert executable is not None
        tools = Path(executable).parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / 'source.pdf', root / 'out.pdf'
            write_pdf(source, pages=1)
            patcher = PDFPatcher(_native_pdf_handler=DefaultPDFHandler(tools))
            self.assertEqual(poppler_path_for(PDFTranslationPipeline(patcher=patcher)), tools)
            with patch.dict(os.environ, {'PATH': folder}):
                asyncio.run(patcher.patch(source, target, []))
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertIsNone(current_poppler_path())

    def test_custom_handler_contract_requires_no_new_configuration_attribute(self):
        class OpaqueHandler:
            def __getattr__(self, name):
                raise AssertionError(f'custom extension must not be probed for {name}')
        self.assertIsNone(poppler_path_for(OpaqueHandler()))

    def test_path_lookup_changes_do_not_reuse_a_different_configuration(self):
        @pdf_operation
        async def operation(source, empty_directory):
            with patch.dict(os.environ, {'PATH': str(empty_directory)}):
                with self.assertRaises(PDFPreflightError):
                    check_pdf(source)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source, pages=1)
            asyncio.run(operation(source, folder))

    def test_extraction_facade_preserves_engine_configuration_and_native_text(self):
        from pdf_craft.transform import PDFExtractionEngine
        from tests.extraction_helpers import make_extraction
        executable = shutil.which('pdfinfo')
        assert executable is not None
        tools = Path(executable).parent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.pdf'
            write_pdf(source, pages=1)
            with patch('pdf_craft.transform.OCR', return_value=Mock(is_vendor=False)):
                engine = PDFExtractionEngine(pdf_handler=DefaultPDFHandler(tools))

            async def extract(**kwargs):
                self.assertIsNotNone(kwargs['native_pdf_text'])
                make_extraction(kwargs['analysing_path'] / 'extraction')
                return None, None, None, None, 'metering'

            native = PopplerDocument.render_page
            with patch.dict(os.environ, {'PATH': folder}), patch.object(
                engine, 'extract_package_async', side_effect=extract,
            ), patch.object(PopplerDocument, 'render_page', autospec=True, side_effect=native) as render:
                result = asyncio.run(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    source, root / 'book.pcex',
                ))
            self.assertIsNotNone(result)
            self.assertEqual(render.call_count, 1)

    def test_inherited_directory_reaches_real_qt_composition_process(self):
        executable, ghostscript = shutil.which('pdfinfo'), shutil.which('gs')
        assert executable is not None and ghostscript is not None
        tools = Path(executable).parent
        class Operation:
            def _preflight_poppler_path(self):
                return tools

            @pdf_operation
            async def run(self, source, target):
                await PDFPatcher().patch(source, target, [
                    PDFReplacement(1, (10, 10, 100, 40), 'Translated text', (200, 300)),
                ])

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / 'source.pdf', root / 'out.pdf'
            write_pdf(source, pages=1)
            # The output compiler is independently required; only Poppler is
            # intentionally unavailable through PATH, including in the child.
            (root / 'gs').symlink_to(ghostscript)
            with patch.dict(os.environ, {'PATH': folder}):
                asyncio.run(Operation().run(source, target))
            import pypdf
            with pypdf.PdfReader(target) as reader:
                page = cast(Any, reader.pages[0])
                self.assertIn('Translated text', page.extract_text())  # pylint: disable=no-member
