"""Book-level notifications at finalized extraction and translation boundaries."""
# Each loop iteration finishes its async work before the next one.
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import (
    AsyncPDFCraft, DocumentAuthor, DocumentMetadata, EpubFragmentContent,
    EpubTranslation, ExtractionOptions, MetadataContent, PDFCraft, ConcurrentExecutor, FixedCapacity,
)
from pdf_craft.common import save_xml
from pdf_craft.document import write_manifest
from pdf_craft.error import PDFError
from pdf_craft.pdf import PDFDocumentMetadata, encode
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.runtime import IO_DOMAIN
from pdf_craft.transform import PDFExtractionEngine
from pdf_craft.transformer import XMLTranslator, SubmitKind
from tests.extraction_helpers import make_extraction
from tests.test_paragraph_content import text, translator, translated
from tests.test_stream_callbacks import page, epub
from pdf_craft.extractor.chapter.chapter import Chapter, encode as encode_chapter


def native_metadata():
    return PDFDocumentMetadata('Native title', 'Native description', 'Native publisher', 'ISBN',
                               ['Native author'], ['Editor'], ['Translator'], datetime.now(timezone.utc))


class FakeOCR:
    is_vendor = False
    last_page_pixel_sizes = {1: (100, 100), 2: (100, 100)}

    def __init__(self, events, native=None):
        self.events = events
        self.native = native

    def metadata(self, _path):
        self.events.append('native')
        if isinstance(self.native, Exception):
            raise self.native
        return self.native

    def recognize(self, **kwargs):
        self.events.append('ocr')
        folder = kwargs['ocr_path']
        folder.mkdir(parents=True, exist_ok=True)
        for index in (1, 2):
            yield OCREvent(OCREventKind.START, index, 2)
            save_xml(encode(page(index)), folder / f'page_{index}.xml')
            yield OCREvent(OCREventKind.COMPLETE, index, 2)


def engine(ocr):
    instance = PDFExtractionEngine.__new__(PDFExtractionEngine)
    instance._ocr = ocr
    return instance


def book(root):
    source = make_extraction(root, page_pixel_sizes={1: (100, 100)}, with_toc=True)
    save_xml(encode_chapter(Chapter(None, -1, [text(0, 'Body.')])), root / 'chapters/chapter_head.xml')
    write_manifest(root, document_metadata=DocumentMetadata(
        title='Title', description='Description', publisher='Publisher', isbn='9780000000000',
        authors=(DocumentAuthor('Author', 'Original author', 'Country'),),
        editors=('Editor',), subjects=('Subject A', 'Subject B'), language='en',
        modified=datetime(2020, 1, 1, tzinfo=timezone.utc),
    ))
    return source


def epub_book(path, *, empty=False):
    epub(path)
    with ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    fields = '' if empty else '''<dc:title>Title</dc:title><dc:creator>Author A</dc:creator>
      <dc:creator>Author B</dc:creator><dc:publisher>Publisher</dc:publisher>
      <dc:subject>Subject A</dc:subject><dc:subject>Subject B</dc:subject>
      <dc:language>en</dc:language><dc:date>2020</dc:date>
      <dc:identifier opf:scheme="ISBN">9780000000000</dc:identifier>
      <dc:contributor opf:role="trl">Translator</dc:contributor>
      <meta property="custom:setting">preserved extension</meta>'''
    files['EPUB/book.opf'] = ('''<package xmlns:dc="http://purl.org/dc/elements/1.1/"
       xmlns:opf="http://www.idpf.org/2007/opf"><metadata>''' + fields + '''</metadata>
       <manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
       <item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest>
       <spine><itemref idref="a"/></spine></package>''').encode()
    files['EPUB/a.xhtml'] = b'<html><head><title>Document title</title></head><body><p>Body.</p></body></html>'
    with ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)


class MetadataCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_notification_precedes_ocr_is_awaited_and_matches_manifest(self):
        events, received = [], []
        started, release = asyncio.Event(), asyncio.Event()
        async def callback(content):
            events.append('metadata')
            received.append(content)
            started.set()
            await release.wait()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = asyncio.create_task(AsyncPDFCraft.from_engine(engine(FakeOCR(events, native_metadata()))).extract_pdf(
                root / 'a.pdf', root / 'a.pcex', ExtractionOptions(includes_furniture=False, on_metadata=callback)))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertEqual(events, ['native', 'metadata'])
                self.assertFalse((root / 'a.pcex').exists())
            finally:
                release.set()
                result = await task
            self.assertEqual(len(received), 1)
            self.assertIsNone(received[0].original)
            with result._materialize() as paths:
                metadata = json.loads(paths.manifest.read_text())['document']
            self.assertEqual(metadata['title'], received[0].metadata.title)
            self.assertEqual(metadata['authors'][0]['name'], 'Native author')

    async def test_llm_waits_merges_fallback_and_notifies_once(self):
        for fail in (False, True):
            events, received = [], []
            async def extract(*_args):
                self.assertIn('ocr', events)
                self.assertEqual(received, [])
                events.append('llm')
                if fail:
                    raise RuntimeError('LLM failed')
                return DocumentMetadata(title='Printed title', subjects=('Printed subject',))
            def callback(content):
                events.append('metadata')
                received.append(content)
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory, patch(
                    'pdf_craft.transform.extract_book_metadata_from_ocr', new=extract):
                root = Path(directory)
                await AsyncPDFCraft.from_engine(engine(FakeOCR(events, native_metadata()))).extract_pdf(
                    root / 'a.pdf', root / 'a.pcex', ExtractionOptions(
                        includes_furniture=False, extract_book_metadata=True,
                        metadata_llm=translator()._translation_llm, on_metadata=callback), error_policy=True)
                self.assertEqual(len(received), 1)
                self.assertEqual(events[-1], 'metadata')
                self.assertLess(events.index('native'), events.index('llm'))
                self.assertEqual(received[0].metadata.title, 'Native title' if fail else 'Printed title')
                self.assertEqual(received[0].metadata.publisher, 'Native publisher')
                self.assertEqual(received[0].metadata.authors, (DocumentAuthor('Native author'),))

    async def test_empty_and_failed_native_metadata_notify_empty(self):
        for native in (None, PDFError('unreadable PDF metadata')):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                received = []
                await AsyncPDFCraft.from_engine(engine(FakeOCR([], native))).extract_pdf(
                    root / 'a.pdf', root / 'a.pcex', ExtractionOptions(
                        includes_furniture=False, on_metadata=received.append), error_policy=True)
                self.assertEqual(received, [MetadataContent(DocumentMetadata())])

    async def test_no_callback_keeps_native_only_metadata_optional(self):
        events = []
        with tempfile.TemporaryDirectory() as directory, patch(
                'pdf_craft.transform.MetadataContent', side_effect=AssertionError('unused snapshot')):
            root = Path(directory)
            await AsyncPDFCraft.from_engine(engine(FakeOCR(events, native_metadata()))).extract_pdf(
                root / 'a.pdf', root / 'a.pcex', ExtractionOptions(includes_furniture=False))
        self.assertNotIn('native', events)

    async def test_callback_failure_is_not_swallowed_by_llm_fallback(self):
        for use_llm in (False, True):
            events = []
            async def fail(_content):
                raise ValueError('consumer failed')
            with self.subTest(use_llm=use_llm), tempfile.TemporaryDirectory() as directory, patch(
                    'pdf_craft.transform.extract_book_metadata_from_ocr', new=AsyncMock(return_value=DocumentMetadata(title='Printed'))):
                root = Path(directory)
                with self.assertRaisesRegex(ValueError, 'consumer failed'):
                    await AsyncPDFCraft.from_engine(engine(FakeOCR(events, native_metadata()))).extract_pdf(
                        root / 'a.pdf', root / 'a.pcex', ExtractionOptions(
                            includes_furniture=False, extract_book_metadata=use_llm,
                            metadata_llm=translator()._translation_llm if use_llm else None, on_metadata=fail))
                self.assertFalse((root / 'a.pcex').exists())
                if not use_llm:
                    self.assertNotIn('ocr', events)

    async def test_metadata_callback_cancellation_stops_extraction(self):
        entered = asyncio.Event()
        async def wait(_content):
            entered.set()
            await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            task = asyncio.create_task(AsyncPDFCraft.from_engine(engine(FakeOCR(events, native_metadata()))).extract_pdf(
                root / 'a.pdf', root / 'a.pcex', ExtractionOptions(includes_furniture=False, on_metadata=wait)))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertNotIn('ocr', events)
            self.assertFalse((root / 'a.pcex').exists())

    async def test_pcex_metadata_callback_failure_preserves_output(self):
        async def fail(_content):
            raise ValueError('consumer failed')
        with tempfile.TemporaryDirectory() as directory, patch.object(
                XMLTranslator, '_translate_inline_segments_async', new=translated):
            root = Path(directory)
            source = book(root / 'source')
            output = root / 'out.md'
            output.write_text('existing output')
            with self.assertRaisesRegex(ValueError, 'consumer failed'):
                await AsyncPDFCraft().render_markdown(source, output, translator=translator(), on_metadata=fail)
            self.assertEqual(output.read_text(), 'existing output')

    async def test_shared_pcex_translation_notifies_complete_metadata_once(self):
        import pypdf
        from pdf_craft.pipeline.pdf.patcher import PDFPatcher
        async def patch_pdf(_self, _source, target, replacements, **_kwargs):
            await IO_DOMAIN.run(list, replacements)
            target.write_bytes(b'PDF output')
        for kind in ('pcex', 'markdown', 'epub', 'pdf'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory, patch.object(
                    XMLTranslator, '_translate_inline_segments_async', new=translated), patch.object(
                    PDFPatcher, 'patch', new=patch_pdf):
                root = Path(directory)
                source = book(root / 'source')
                before = (root / 'source/manifest.json').read_bytes()
                received = []
                options: dict[str, Any] = dict(translator=translator(), on_metadata=received.append)
                craft = AsyncPDFCraft(error_policy=True, )
                if kind == 'pcex':
                    output = await craft.translate_extraction(source, root / 'out.pcex', target_language='fr', with_anchored=False, **options)
                    with output._materialize() as paths:
                        layer = (await craft.list_translations(output))[0].id
                        overlay = json.loads((paths.translations / layer / 'metadata.json').read_text())
                        self.assertEqual(overlay['language'], 'fr')
                elif kind == 'pdf':
                    pdf = root / 'source.pdf'
                    writer = pypdf.PdfWriter()
                    writer.add_blank_page(100, 100)
                    writer.write(pdf)
                    await craft.patch_pdf_with_extraction(pdf, source, root / 'out.pdf', **options)
                else:
                    await getattr(craft, 'render_' + kind)(source, root / ('out.' + ('md' if kind == 'markdown' else 'epub')), with_anchored=False, **options)
                self.assertEqual(len(received), 1)
                value = received[0]
                self.assertEqual(value.original.title, 'Title')
                self.assertEqual(value.metadata.title, '译:Title')
                self.assertEqual(value.metadata.language, 'fr' if kind == 'pcex' else 'zh')
                self.assertEqual(value.metadata.isbn, value.original.isbn)
                self.assertEqual(value.metadata.authors, value.original.authors)
                self.assertEqual(value.metadata.modified, datetime(2020, 1, 1, tzinfo=timezone.utc))
                self.assertEqual((root / 'source/manifest.json').read_bytes(), before)

    async def test_metadata_translation_without_callback_does_not_build_snapshot(self):
        from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
        with patch.object(XMLTranslator, '_translate_inline_segments_async', new=translated), patch(
                'pdf_craft.document.metadata.metadata_from_mapping', side_effect=AssertionError('unused')):
            overlay = await NarrativeXMLTransformer(translator()).translate_metadata({'title': 'Title'})
        self.assertEqual(overlay['title'], '译:Title')

    async def test_epub_metadata_once_preserves_repeated_and_extension_fields(self):
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as directory, patch.object(
                    XMLTranslator, '_translate_inline_segments_async', new=translated):
                root = Path(directory)
                source = root / 'source.epub'
                epub_book(source, empty=empty)
                received = []
                await AsyncPDFCraft().translate_epub(source, root / 'out.epub', target_language='zh',
                    submit=SubmitKind.REPLACE, llm=translator()._translation_llm,
                    executor=ConcurrentExecutor(FixedCapacity(2)), on_metadata=received.append)
                self.assertEqual(len(received), 1)
                value = received[0]
                self.assertEqual(value.metadata.language, 'zh')
                with ZipFile(root / 'out.epub') as archive:
                    opf = fromstring(archive.read('EPUB/book.opf'))
                self.assertEqual(opf.findtext('.//{*}language'), 'zh')
                if empty:
                    self.assertEqual(value.original, DocumentMetadata())
                    self.assertIsNone(value.metadata.title)
                else:
                    self.assertEqual(value.original.title, 'Title')
                    self.assertEqual(value.metadata.title, '译:Title')
                    self.assertEqual(tuple(author.name for author in value.metadata.authors), ('译:Author A', '译:Author B'))
                    self.assertEqual(value.metadata.subjects, ('译:Subject A', '译:Subject B'))
                    self.assertEqual(value.metadata.isbn, '9780000000000')
                    self.assertEqual(value.metadata.translators, ('Translator',))
                    self.assertEqual(value.metadata.publication_date, '2020')
                    self.assertEqual(opf.findtext('.//{*}meta[@property="custom:setting"]'), 'preserved extension')
                    self.assertEqual(opf.findtext('.//{*}identifier'), '9780000000000')

    async def test_epub_callback_failure_preserves_existing_output_and_no_callback_skips_projection(self):
        async def fail(_content):
            raise ValueError('metadata consumer failed')
        with tempfile.TemporaryDirectory() as directory, patch.object(
                XMLTranslator, '_translate_inline_segments_async', new=translated):
            root = Path(directory)
            epub_book(root / 'source.epub')
            target = root / 'out.epub'
            target.write_bytes(b'existing output')
            options: dict[str, Any] = dict(target_language='zh', submit=SubmitKind.REPLACE,
                           llm=translator()._translation_llm, executor=ConcurrentExecutor(FixedCapacity(2)))
            for mode in (SubmitKind.REPLACE, SubmitKind.APPEND_TEXT, SubmitKind.APPEND_BLOCK):
                with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'metadata consumer failed'):
                    await AsyncPDFCraft().translate_epub(root / 'source.epub', target,
                        on_metadata=fail, **{**options, 'submit': mode})
                self.assertEqual(target.read_bytes(), b'existing output')
            with patch('pdf_craft.pipeline.epub.adapter.metadata.translated_metadata_content',
                       side_effect=AssertionError('unused snapshot')):
                await AsyncPDFCraft().translate_epub(root / 'source.epub', target, **options)

    async def test_epub_metadata_callback_is_separate_from_bilingual_writeback(self):
        async def preserve_publisher(instance, inline_segments, **kwargs):
            mappings = await translated(instance, inline_segments, **kwargs)
            return [None if any(part.text == 'Publisher' for part in segment) else mapping
                    for segment, mapping in zip(inline_segments, mappings, strict=True)]

        for mode in (SubmitKind.REPLACE, SubmitKind.APPEND_TEXT, SubmitKind.APPEND_BLOCK):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, patch.object(
                    XMLTranslator, '_translate_inline_segments_async', new=preserve_publisher):
                root = Path(directory)
                source, target = root / 'source.epub', root / 'out.epub'
                epub_book(source)
                received = []
                await AsyncPDFCraft().translate_epub(source, target, target_language='zh',
                    submit=mode, llm=translator()._translation_llm,
                    executor=ConcurrentExecutor(FixedCapacity(2)), on_metadata=received.append)

                self.assertEqual(len(received), 1)
                content = received[0]
                self.assertEqual(content.original.title, 'Title')
                self.assertEqual(content.metadata.title, '译:Title')
                self.assertEqual(tuple(author.name for author in content.metadata.authors),
                                 ('译:Author A', '译:Author B'))
                self.assertEqual(content.metadata.subjects, ('译:Subject A', '译:Subject B'))
                self.assertEqual(content.metadata.isbn, content.original.isbn)
                self.assertEqual(content.metadata.publisher, 'Publisher')
                self.assertEqual(content.metadata.language, 'zh')
                with ZipFile(target) as archive:
                    opf = fromstring(archive.read('EPUB/book.opf'))
                expected = '译:Title' if mode == SubmitKind.REPLACE else 'Title 译:Title'
                self.assertEqual(opf.findtext('.//{*}title'), expected)
                self.assertEqual(opf.findtext('.//{*}publisher'), 'Publisher')
                expected_authors = ['译:Author A', '译:Author B'] if mode == SubmitKind.REPLACE else [
                    'Author A 译:Author A', 'Author B 译:Author B']
                self.assertEqual([node.text for node in opf.findall('.//{*}creator')], expected_authors)


class MetadataValueTests(unittest.TestCase):
    def test_fragment_name_is_a_compatible_alias_and_metadata_is_frozen(self):
        self.assertIs(EpubTranslation, EpubFragmentContent)
        self.assertEqual(EpubFragmentContent('cfi', 'a', 'b').translated, 'b')
        value = MetadataContent(DocumentMetadata())
        with self.assertRaises(FrozenInstanceError):
            setattr(value, 'metadata', DocumentMetadata(title='changed'))

    def test_sync_extraction_awaits_async_metadata_callback(self):
        received = []
        async def callback(content):
            await asyncio.sleep(0)
            received.append(content)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            PDFCraft.from_engine(engine(FakeOCR([], native_metadata()))).extract_pdf(
                root / 'a.pdf', root / 'a.pcex', ExtractionOptions(
                    includes_furniture=False, on_metadata=callback))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].metadata.title, 'Native title')
