"""External text must be safe before JSON/XML persistence or callbacks."""
# pylint: disable=protected-access
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from epub_generator import BookMeta

from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM, PDFCraft
from pdf_craft.common import safe_text, save_xml
from pdf_craft.document import DocumentAuthor, DocumentMetadata
from pdf_craft.document.package import write_manifest
from pdf_craft.document.render import _read_metadata_overlay
from pdf_craft.extractor.chapter.chapter import Chapter, encode
from pdf_craft.extractor.metadata import merge_ocr_and_pdf_metadata, _Evidence
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.pdf.furniture import _native_pages
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.page_labels import read_page_labels
from pdf_craft.pdf.types import PDFDocumentMetadata
from pdf_craft.renderer.epub.renderer import _merge_book_meta
from tests.extraction_helpers import make_extraction
from tests.test_paragraph_content import text


def write_body(root):
    save_xml(encode(Chapter(None, -1, [text(0, 'Readable body.')])),
             root / 'chapters/chapter_head.xml')


class SafeTextTests(unittest.TestCase):
    def test_complete_unicode_domain_and_idempotence(self):
        # Exhaustive against the XML 1.0 Char production, not a second regex.
        original = ''.join(map(chr, range(0x110000)))
        expected = ''.join(chr(n) for n in range(0x110000)
                           if n in (9, 10, 13) or 0x20 <= n <= 0xd7ff
                           or 0xe000 <= n <= 0xfffd or 0x10000 <= n <= 0x10ffff)
        self.assertEqual(safe_text(original), expected)
        self.assertEqual(safe_text(expected), expected)

    def test_preserves_text_and_only_serialization_escapes_markup(self):
        expected = '中文 😀\u200b\u200d\t\n\r &<>"\''
        value = safe_text('\x00' + expected + '\x14\ud800\ufffe\uffff')
        self.assertEqual(value, expected)
        root = ET.Element('root', {'value': value})
        root.text = value
        child = ET.SubElement(root, 'child')
        child.tail = value
        parsed = ET.fromstring(ET.tostring(root))
        self.assertEqual(parsed.attrib['value'], expected)
        # XML normalizes literal CR to LF in character data.
        self.assertEqual(parsed.text, expected.replace('\r', '\n'))
        self.assertEqual(parsed[0].tail, expected.replace('\r', '\n'))

    def test_native_and_standard_metadata_clean_all_text_fields(self):
        native = PDFDocumentMetadata('T\x00', 'D\x14', 'P\ud800', 'I\uffff',
                                     ['A\x01'], ['E\x02'], ['X\x03'], datetime.now(timezone.utc))
        adopted = merge_ocr_and_pdf_metadata(None, native)
        self.assertEqual((adopted.title, adopted.description, adopted.publisher, adopted.isbn),
                         ('T', 'D', 'P', 'I'))
        self.assertEqual(adopted.authors, (DocumentAuthor('A'),))
        self.assertEqual(adopted.editors, ('E',))
        self.assertEqual(adopted.translators, ('X',))
        author = DocumentAuthor('A\x00', 'B\x01', 'C\x02')
        metadata = DocumentMetadata(original_title='O\x00', rights='R\x14',
                                    authors=(author,), subjects=('S\udfff',))
        self.assertEqual(asdict(author), {'name': 'A', 'original_name': 'B', 'nationality': 'C'})
        self.assertEqual((metadata.original_title, metadata.rights, metadata.subjects), ('O', 'R', ('S',)))
        self.assertEqual(_Evidence(value='value\x00', page_index=1, evidence='text\x14').value, 'value')

    def test_explicit_book_meta_is_adopted_without_mutating_caller_or_dates(self):
        modified = datetime(2020, 1, 1, tzinfo=timezone.utc)
        source = BookMeta(title='T\x00', description='D\x14', authors=['A\ud800'], modified=modified)
        result = _merge_book_meta(None, source)
        assert result is not None
        self.assertEqual((result.title, result.description, result.authors), ('T', 'D', ['A']))
        self.assertEqual(result.modified, modified)
        self.assertEqual(source.title, 'T\x00')

    def test_ocr_cleans_html_text_and_nested_blocks(self):
        node = PageExtractorNode.__new__(PageExtractorNode)
        block = SimpleNamespace(html='a\x00<table><tr><td>b\x14</td></tr></table>', text=None,
                                children=[SimpleNamespace(html=None, text='中文\ud800😀')])
        self.assertEqual(node._normalize_block_text(block),
                         'a<table><tr><td>b</td></tr></table>\n中文😀')

    def test_page_labels_are_cleaned_and_native_allocations_are_freed(self):
        library, glib, gobject = MagicMock(), MagicMock(), MagicMock()
        library.poppler_document_new_from_file.return_value = 1
        library.poppler_document_get_page.return_value = 2
        library.poppler_page_get_label.return_value = 3
        with patch('pdf_craft.pdf.page_labels.find_library', return_value='unused'), patch(
            'pdf_craft.pdf.page_labels.ctypes.CDLL', side_effect=[library, glib, gobject],
        ), patch('pdf_craft.pdf.page_labels.ctypes.string_at', return_value=b'prefix\x14iv\x01'):
            self.assertEqual(read_page_labels(Path('unused.pdf'), 1), {1: 'prefixiv'})
        glib.g_free.assert_called_once_with(3)
        self.assertEqual(gobject.g_object_unref.call_count, 2)

    def test_external_translation_overlay_is_clean_without_filling_missing_fields(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'metadata.json'
            path.write_text(json.dumps({'title': '译\x00', 'authors': ['人\x14']}), encoding='utf-8')
            self.assertEqual(_read_metadata_overlay(path), {'title': '译', 'authors': ['人']})

    def test_native_xml_does_not_drop_valid_words_because_of_bad_metadata(self):
        incoming = b'''<html><head><title>bad\x00title\x14</title></head><body>
        <page width="100" height="100"><line xMin="1" yMin="2" xMax="70" yMax="12">
        <word xMin="1" yMin="2" xMax="70" yMax="12">Real\x01word</word>
        </line></page></body></html>'''
        with self.assertRaises(ET.ParseError):
            ET.fromstring(incoming)
        with TemporaryDirectory() as directory:
            pages = _native_pages(Path('unused.pdf'), Path(directory),
                                  native_pdf_text=incoming, native_pdf_text_prepared=True)
        self.assertEqual(pages[1][0].content, 'Realword')
        self.assertEqual(pages[1][0].det, (4, 8, 292, 50))

    def test_bad_xml_structure_is_not_silently_repaired(self):
        with TemporaryDirectory() as directory:
            pages = _native_pages(Path('unused.pdf'), Path(directory),
                                  native_pdf_text=b'<page><broken></page>', native_pdf_text_prepared=True)
        self.assertEqual(pages, {})

    def test_old_manifest_is_cleaned_on_admission_and_epub_retains_body(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source')
            write_body(root / 'source')
            manifest = root / 'source/manifest.json'
            data = json.loads(manifest.read_text())
            data['document'].update(description='D\x00\x14', title='T\ud800',
                                    authors=[{'name': 'A\x01', 'original_name': None, 'nationality': None}])
            manifest.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(source._document_metadata()['description'], 'D')
            PDFCraft().render_epub(source, root / 'result.epub')
            with ZipFile(root / 'result.epub') as archive:
                opf = ET.fromstring(archive.read('OEBPS/content.opf'))
                self.assertEqual(opf.findtext('.//{*}description'), 'D')
                self.assertTrue(any(b'Readable body.' in archive.read(name)
                                    for name in archive.namelist() if name.endswith('.xhtml')))
            # Reading external metadata must not rewrite the original archive.
            self.assertEqual(json.loads(manifest.read_text())['document']['description'], 'D\x00\x14')

    def test_new_manifest_is_clean_before_json_persistence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            make_extraction(root)
            write_manifest(root, book_meta=BookMeta(description='D\x00\x14', authors=['A\x01']))
            data = json.loads((root / 'manifest.json').read_text())['document']
            self.assertEqual(data['description'], 'D')
            self.assertEqual(data['authors'][0]['name'], 'A')


class SafeModelResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_and_cached_response_are_clean_on_admission(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base',
                         cache_path=root / 'cache', log_dir_path=root / 'logs')
            runtime = LLMRuntime(config, executor=ConcurrentExecutor(FixedCapacity(1)))
            with patch.object(runtime, '_invoke_for_request', AsyncMock(return_value='body\x00\x14😀')) as invoke:
                self.assertEqual(await runtime.request('request'), 'body😀')
                self.assertEqual(invoke.await_count, 1)
                cache = next((root / 'cache').glob('*.txt'))
                cache.write_text('old\x00\x14😀', encoding='utf-8')
                self.assertEqual(await runtime.request('request'), 'old😀')
                self.assertEqual(invoke.await_count, 1)
