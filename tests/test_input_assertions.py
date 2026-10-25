# pylint: disable=protected-access
"""Optional assertions exercise real small PDFs/EPUBs, without OCR or LLMs."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zipfile import BadZipFile, ZipFile

import pypdf
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, NameObject

from pdf_craft import (
    assert_pdf_readable, assert_pdf_renderable, assert_pdf_editable,
    assert_epub_readable, pdf_pages_count, AsyncPDFCraft,
)
from pdf_craft.pdf.poppler import PopplerDocument
from tests.pdf_assertion_helpers import write_pdf
from tests.test_epub_optional_cfi import book, document as epub_document


class PDFAssertionTests(unittest.TestCase):
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
                assert_pdf_renderable(source)
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


    def test_success_does_not_modify_source_or_leave_trial_files(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source)
            original = source.read_bytes()
            for check in (assert_pdf_readable, assert_pdf_renderable, assert_pdf_editable):
                with self.subTest(check=check.__name__):
                    self.assertIsNone(check(source))
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_original_xref_damage_is_readable_but_not_editable(self):
        source = Path(__file__).parent / 'assets/pdf/xref-offset-997ad598.pdf'
        self.assertIsNone(assert_pdf_readable(source))
        self.assertEqual(pdf_pages_count(source), 251)
        with self.assertRaises(PdfReadError):
            assert_pdf_editable(source)

    def test_bad_input_is_reported_by_the_actual_backend(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            source.write_text('This is not a PDF')
            for check, error in ((assert_pdf_readable, RuntimeError),
                                 (assert_pdf_renderable, RuntimeError),
                                 (assert_pdf_editable, PdfReadError)):
                with self.subTest(check=check.__name__), self.assertRaises(error):
                    check(source)
            with self.assertRaises(FileNotFoundError):
                assert_pdf_readable(source, poppler_path=Path(folder) / 'missing')

    def test_later_page_render_error_does_not_prevent_plain_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'broken-page.pdf'
            writer = pypdf.PdfWriter()
            writer.add_blank_page(200, 300)
            page = writer.add_blank_page(200, 300)
            content = DecodedStreamObject()
            content.set_data(b'q /MissingXObject Do Q')
            page[NameObject('/Contents')] = writer._add_object(content)  # pylint: disable=protected-access
            writer.write(source)
            writer.close()
            self.assertIsNone(assert_pdf_readable(source))
            self.assertEqual(pdf_pages_count(source), 2)
            with self.assertRaisesRegex(RuntimeError, 'MissingXObject'):
                assert_pdf_renderable(source)

    def test_read_and_conversion_do_not_run_assertions_or_eager_renders(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source)
            engine = Mock(spec=["extract_package_async"])
            engine.extract_package_async = AsyncMock(side_effect=LookupError('actual extraction reached'))
            with patch.object(PopplerDocument, 'render_page', side_effect=AssertionError('eager render')), \
                 patch('pypdf.PdfReader', side_effect=AssertionError('unexpected pypdf read')):
                self.assertEqual(pdf_pages_count(source), 2)
                with self.assertRaisesRegex(LookupError, 'actual extraction reached'):
                    asyncio.run(AsyncPDFCraft.from_engine(engine).extract_pdf(
                        source, Path(folder) / 'book.pcex'))
            engine.extract_package_async.assert_awaited_once()

    def test_write_and_reopen_failures_propagate_unchanged_and_clean_trial(self):
        real_reader = pypdf.PdfReader
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            write_pdf(source)
            original = source.read_bytes()
            trial_paths = []

            def failing_write(_writer, target):
                trial_paths.append(target)
                Path(target).write_bytes(b'incomplete')
                raise failure

            failure = OSError('trial write failed')
            with patch.object(pypdf.PdfWriter, 'write', failing_write), self.assertRaises(OSError) as caught:
                assert_pdf_editable(source)
            self.assertIs(caught.exception, failure)
            self.assertFalse(trial_paths[0].parent.exists())

            def fail_reopen(path, *args, **kwargs):
                if Path(path) != source:
                    trial_paths.append(Path(path))
                    raise failure
                return real_reader(path, *args, **kwargs)

            failure = ValueError('trial reopen failed')
            with patch('pypdf.PdfReader', side_effect=fail_reopen), self.assertRaises(ValueError) as caught:
                assert_pdf_editable(source)
            self.assertIs(caught.exception, failure)
            self.assertFalse(trial_paths[-1].parent.exists())
            self.assertEqual(source.read_bytes(), original)


class EPUBAssertionTests(unittest.TestCase):
    def test_reads_all_spine_documents_without_creating_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.epub'
            book(source, [('first.xhtml', 'application/xhtml+xml', epub_document('First')),
                          ('second.html', 'text/html', epub_document('Second&nbsp;body', html=True))])
            original = source.read_bytes()
            self.assertIsNone(assert_epub_readable(source))
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_later_broken_or_missing_chapter_is_not_hidden_by_lazy_iteration(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.epub'
            for missing in (False, True):
                book(source, [('first.xhtml', 'application/xhtml+xml', epub_document('First')),
                              ('second.xhtml', 'application/xhtml+xml', '<html><body><p>broken</body>')])
                if missing:
                    with ZipFile(source) as archive:
                        entries = [(i, archive.read(i)) for i in archive.infolist()
                                   if i.filename != 'EPUB/second.xhtml']
                    with ZipFile(source, 'w') as archive:
                        for info, content in entries:
                            archive.writestr(info, content)
                original = source.read_bytes()
                with self.subTest(missing=missing), self.assertRaises(KeyError if missing else ValueError):
                    assert_epub_readable(source)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_zip_container_opf_and_toc_errors_propagate(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'bad.epub'
            source.write_text('not a zip')
            with self.assertRaises(BadZipFile):
                assert_epub_readable(source)
            for member in ('META-INF/container.xml', 'EPUB/book.opf', 'EPUB/toc.ncx'):
                book(source, [('first.xhtml', 'application/xhtml+xml', epub_document('First'))])
                with ZipFile(source) as archive:
                    entries = [(i, archive.read(i)) for i in archive.infolist() if i.filename != member]
                with ZipFile(source, 'w') as archive:
                    for info, content in entries:
                        archive.writestr(info, content)
                with self.subTest(member=member), self.assertRaises(KeyError):
                    assert_epub_readable(source)
