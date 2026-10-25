"""Real stream boundaries with deterministic producers and no hosted requests."""
# pylint: disable=protected-access

import asyncio
import tempfile
import unittest
from functools import partial
from resource_segmentation import split
from pathlib import Path
from typing import Any
from unittest.mock import patch
from xml.etree.ElementTree import Element, fromstring, tostring
from zipfile import ZipFile

from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM
from pdf_craft.common.stream import DiskDelivery, DiskStream
from pdf_craft.extractor.chapter.generation import _analyse_chapter_pages, _generate_chapters
from pdf_craft.extractor.chapter.chapter import encode
from pdf_craft.extractor.chapter.live import LiveExtraction
from pdf_craft.extractor.toc.types import TocInfo
from pdf_craft.pdf import decode, encode as encode_page
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.common import save_xml
from pdf_craft.pipeline.epub.translation.translator import translate
from pdf_craft.transformer import SubmitKind
from pdf_craft.transformer.xml_translator.segment import search_text_segments


FOOTNOTE_MARKS = (
    'Ⅰ', 'ⅰ', '①', '⓵', '➀', '➊', '❶', '㈠', '㊀',
    '１', '𝟭', '𝟏', '𝟙', '🄂', '*', '**', '***', '****', '*****', '******',
)


def page(index, text='Complete sentence.', note=''):
    return decode(fromstring(
        f'<page index="{index}"><body><layout ref="text" det="0,0,100,20">{text}</layout></body>'
        f'<footnotes><layout ref="text" det="0,40,100,60">{note}</layout></footnotes></page>'
    ))


def epub(path):
    with ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml',
                         '<container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
        archive.writestr('EPUB/book.opf',
                         '<package><metadata/><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest>'
                         '<spine><itemref idref="a"/></spine></package>')
        archive.writestr('EPUB/toc.ncx', '<ncx><navMap/></ncx>')
        archive.writestr('EPUB/a.xhtml',
                         '<html xmlns="http://www.w3.org/1999/xhtml"><head/><body>' +
                         ''.join(f'<p>Sentence {i}.</p>' for i in range(8)) + '</body></html>')


class StreamCallbacksTests(unittest.IsolatedAsyncioTestCase):
    async def test_disk_backlog_does_not_wait_for_slow_callback(self):
        entered, release = asyncio.Event(), asyncio.Event()
        received = []

        async def callback(value):
            entered.set()
            await release.wait()
            received.append(value)

        with tempfile.TemporaryDirectory() as directory:
            delivery = DiskDelivery(Path(directory), callback)
            try:
                await delivery.append(0)
                await entered.wait()
                for value in range(1, 8):
                    await delivery.append(value)
                finishing = asyncio.create_task(delivery.finish())
                await asyncio.sleep(0)
                self.assertFalse(finishing.done())
                self.assertEqual(received, [])
                release.set()
                await finishing
                self.assertEqual(received, list(range(8)))
            finally:
                await delivery.cancel()

    async def test_eof_and_producer_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = DiskStream(Path(directory))
            stream.append('a')
            stream.close()
            self.assertEqual([item async for item in stream.aread()], ['a'])
            with self.assertRaises(RuntimeError):
                stream.append('b')
            stream.close(ValueError('producer failed'))
            with self.assertRaisesRegex(ValueError, 'producer failed'):
                _ = [item async for item in stream.aread()]

    async def test_pdf_outputs_before_eof_and_isolates_user_mutation(self):
        received, notified = [], asyncio.Event()
        async def on_body(element):
            received.append(tostring(element))
            element.clear()
            notified.set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / 'ocr'
            pages.mkdir()
            live = LiveExtraction(root / 'live', pages, False, on_body)
            try:
                for index in range(1, 6):
                    save_xml(encode_page(page(index)), pages / f'page_{index}.xml')
                    await live.event(OCREvent(OCREventKind.START, index, 6))
                    await live.event(OCREvent(OCREventKind.COMPLETE, index, 6))
                await asyncio.wait_for(notified.wait(), 2)
                self.assertFalse(live.task.done())
                await live.finish()
                from pdf_craft.extractor.chapter.stream import chapters_from_disk
                roots = [encode(chapter) for chapter in chapters_from_disk(live.database, TocInfo([], []))]
                self.assertEqual(len(received), 5)
                self.assertEqual(len(roots[0].findall('./flow/text')), 5)
                self.assertIn('Complete sentence.', ''.join(roots[0].itertext()))
            finally:
                await live.cancel()

    async def test_out_of_order_ocr_and_eof_tail(self):
        received = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / 'ocr'
            pages.mkdir()
            live = LiveExtraction(root / 'live', pages, False, lambda node: received.append(tostring(node)))
            try:
                for index in range(1, 4):
                    await live.event(OCREvent(OCREventKind.START, index, 3))
                    save_xml(encode_page(page(index, f'Page {index}.')), pages / f'page_{index}.xml')
                for index in (3, 1, 2):
                    await live.event(OCREvent(OCREventKind.COMPLETE, index, 3))
                await live.finish()
                self.assertEqual([next(fromstring(node).iter('fragment')).get('page_index') for node in received], ['1', '2', '3'])
            finally:
                await live.cancel()

    def test_streamed_finalization_matches_page_analysis_with_notes(self):
        for mark in FOOTNOTE_MARKS:
            with self.subTest(mark=mark), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for index in range(1, 4):
                    save_xml(encode_page(page(index, f'Text{mark}. Again{mark}.', f'{mark} Footnote.')),
                             root / f'page_{index}.xml')
                toc = TocInfo([], [])
                analyses = _analyse_chapter_pages(root, toc)[1]
                expected = [tostring(encode(chapter)) for chapter in _generate_chapters(root, toc, analyses)]
                actual = [tostring(encode(chapter)) for chapter in _generate_chapters(root, toc)]
                self.assertEqual(actual, expected)
                chapter = fromstring(actual[0])
                refs = chapter.findall('./flow/text/fragment/ref')
                self.assertEqual([ref.get('id') for ref in refs],
                                 ['1-1', '1-1', '2-1', '2-1', '3-1', '3-1'])
                self.assertEqual(len(chapter.findall('./references/ref')), 3)

    async def test_streamed_callbacks_link_all_supported_footnote_mark_styles(self):
        from pdf_craft.extractor.chapter.stream import chapters_from_disk
        for mark in FOOTNOTE_MARKS:
            with self.subTest(mark=mark), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pages = root / 'ocr'
                pages.mkdir()
                bodies, notes = [], []
                live = LiveExtraction(root / 'live', pages, True,
                                      lambda node, received=bodies: received.append(tostring(node)),
                                      lambda node, received=notes: received.append(tostring(node)))
                try:
                    save_xml(encode_page(page(1, f'Text{mark}. Again{mark}.', f'{mark} Footnote.')),
                             pages / 'page_1.xml')
                    await live.event(OCREvent(OCREventKind.START, 1, 1))
                    await live.event(OCREvent(OCREventKind.COMPLETE, 1, 1))
                    await live.finish()
                    self.assertEqual(len(bodies), 1)
                    body = fromstring(bodies[0])
                    refs = list(body.iter('ref'))
                    self.assertEqual([ref.get('id') for ref in refs], ['1-1', '1-1'])
                    self.assertEqual([fromstring(note).get('id') for note in notes], ['1-1'])
                    self.assertEqual(fromstring(notes[0]).findtext('mark'), mark)
                    note_flow = fromstring(notes[0]).find('flow')
                    assert note_flow is not None
                    self.assertEqual(''.join(note_flow.itertext()), 'Footnote.')
                    final = encode(next(chapters_from_disk(live.database, TocInfo([], []))))
                    final_flow = final.find('flow')
                    assert final_flow is not None
                    self.assertEqual(len(list(final_flow.iter('ref'))), 2)
                finally:
                    await live.cancel()

    async def test_epub_atomic_fragments_have_cfi_and_no_window_duplicates(self):
        produced, received, events = [], [], []
        async def mappings(_translator, inline_segments, **_kwargs):
            result = []
            for segment in inline_segments:
                text = ''.join(item.text for item in segment)
                produced.append(text)
                translated = Element('p')
                translated.text = '译:' + text
                result.append((segment.parent, list(search_text_segments(translated))))
            return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'a.epub', root / 'b.epub'
            epub(source)
            with patch('pdf_craft.transformer.xml_translator.xml_translator.translator.XMLTranslator._translate_inline_segments_async', new=mappings), patch(
                'pdf_craft.transformer.xml_translator.xml_translator.stream_mapper.split', partial(split, gap_rate=.2),
            ):
                await translate(source, target, 'zh', SubmitKind.REPLACE,
                                llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                                executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=500, window=2,
                                on_body=received.append, on_translation_event=events.append)
            from pdf_craft import TranslationEventKind
            self.assertIsNone(events[0].total_characters)
            self.assertEqual(events[-1].kind, TranslationEventKind.COMPLETE)
            self.assertEqual(events[-1].completed_characters, sum(len(f'Sentence {i}.') for i in range(8)))
            self.assertTrue(any(event.kind == TranslationEventKind.ITEM_START for event in events))
            self.assertEqual([item.original for item in received], [f'Sentence {i}.' for i in range(8)])
            self.assertEqual(len({item.epubcfi for item in received}), 8)
            self.assertGreater(len(produced), len(received))
            self.assertTrue(all(item.translated == '译:' + item.original for item in received))
            self.assertTrue(all(item.epubcfi is not None and item.epubcfi.startswith('epubcfi(/6/2!') for item in received))
            from tests.test_epub_cfi import endpoints
            with ZipFile(source) as archive:
                original_root = fromstring(archive.read('EPUB/a.xhtml'))
            for item in received:
                assert item.epubcfi is not None
                start, end = endpoints(original_root, item.epubcfi)
                self.assertEqual(start[1], '')
                self.assertEqual(end[1], item.original)
            self.assertTrue(target.is_file())
            with ZipFile(target) as archive:
                self.assertIn('译:', archive.read('EPUB/a.xhtml').decode())

    async def test_complete_cfi_resolves_spine_order_and_nested_source_files(self):
        from tests.test_epub_cfi import endpoints
        from posixpath import normpath

        async def mappings(_translator, inline_segments, **_kwargs):
            # Source file identity must survive concurrent mapping and
            # restoration of tasks and source DOMs from disk.
            if any('First' in item.text for segment in inline_segments for item in segment):
                await asyncio.sleep(.02)
            return [(segment.parent, list(search_text_segments(segment.create_element())))
                    for segment in inline_segments]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.epub', root / 'target.epub'
            opf_path = 'OPS/package/book.opf'
            with ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip')
                archive.writestr('META-INF/container.xml',
                                 f'<container><rootfiles><rootfile full-path="{opf_path}"/></rootfiles></container>')
                archive.writestr(opf_path,
                    '<package><metadata/><manifest>'
                    '<item id="second" href="../Text/second.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="image" href="cover.png" media-type="image/png"/>'
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                    '<item id="first" href="../Text/first.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><guide/><spine><itemref idref="image"/>'
                    '<itemref idref="first"/><itemref idref="second"/></spine></package>')
                archive.writestr('OPS/package/toc.ncx', '<ncx><navMap/></ncx>')
                archive.writestr('OPS/package/cover.png', b'unused-image')
                for name, text in (('first', 'First 😀 paragraph.'), ('second', 'Second paragraph.')):
                    archive.writestr(f'OPS/Text/{name}.xhtml',
                        f'<html xmlns="http://www.w3.org/1999/xhtml"><head/><body><p>{text}</p></body></html>')
            received = []
            with patch('pdf_craft.transformer.xml_translator.xml_translator.translator.XMLTranslator._translate_inline_segments_async', new=mappings):
                await translate(source, target, 'zh', SubmitKind.REPLACE,
                                llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                                executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=30,
                                on_body=received.append)
            self.assertEqual([item.original for item in received], ['First 😀 paragraph.', 'Second paragraph.'])
            with ZipFile(source) as archive:
                package = fromstring(archive.read(opf_path))
                for index, item in enumerate(received):
                    # Resolve the package component independently of the producer.
                    assert item.epubcfi is not None
                    prefix = item.epubcfi[len('epubcfi('):].split('!')[0]
                    self.assertEqual(prefix, f'/8/{4 + index * 2}')
                    node = package
                    for step in prefix.strip('/').split('/'):
                        node = list(node)[int(step) // 2 - 1]
                    manifest = package.find('manifest')
                    assert manifest is not None
                    href = next(entry.attrib['href'] for entry in manifest
                                if entry.get('id') == node.get('idref'))
                    path = normpath(str(Path(opf_path).parent / href))
                    original_root = fromstring(archive.read(path))
                    start, end = endpoints(original_root, item.epubcfi)
                    self.assertEqual(start[1], '')
                    self.assertEqual(end[1], item.original)
            with ZipFile(target) as archive:
                self.assertFalse(any('/../' in name for name in archive.namelist()))
                self.assertIn('First 😀 paragraph.', archive.read('OPS/Text/first.xhtml').decode())
                self.assertIn('Second paragraph.', archive.read('OPS/Text/second.xhtml').decode())

    async def test_epub_no_callback_skips_cfi_and_failure_preserves_target(self):
        async def mappings(_translator, inline_segments, **_kwargs):
            return [(segment.parent, list(search_text_segments(segment.create_element()))) for segment in inline_segments]
        def failed(_event):
            raise ValueError('consumer failed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'a.epub', root / 'b.epub'
            epub(source)
            options: dict[str, Any] = dict(llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                           executor=ConcurrentExecutor(FixedCapacity(1)))
            with patch('pdf_craft.transformer.xml_translator.xml_translator.translator.XMLTranslator._translate_inline_segments_async', new=mappings):
                with patch('pdf_craft.pipeline.epub.translation.translator.ContentCFILocator', side_effect=AssertionError('unused')):
                    await translate(source, target, 'zh', SubmitKind.REPLACE, **options)
                previous = target.read_bytes()
                with self.assertRaisesRegex(ValueError, 'consumer failed'):
                    await translate(source, target, 'zh', SubmitKind.REPLACE, on_body=failed, **options)
                self.assertEqual(target.read_bytes(), previous)
                self.assertFalse(list(root.glob('*.tmp')))

    async def test_pdf_facade_callbacks_precede_atomic_archive_commit(self):
        from pdf_craft import AsyncPDFCraft, ExtractionOptions
        from pdf_craft.transform import PDFExtractionEngine
        class FakeOCR:
            is_vendor = False
            last_page_pixel_sizes = {1: (100, 100), 2: (100, 100), 3: (100, 100)}
            def recognize(self, **kwargs):
                folder = kwargs['ocr_path']
                folder.mkdir(parents=True, exist_ok=True)
                for index in range(1, 4):
                    yield OCREvent(OCREventKind.START, index, 3)
                    save_xml(encode_page(page(index)), folder / f'page_{index}.xml')
                    yield OCREvent(OCREventKind.COMPLETE, index, 3)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'book.pcex'
            engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
            engine.__dict__["_ocr"] = FakeOCR()
            calls = []
            async def callback(element):
                self.assertFalse(target.exists())
                calls.append(tostring(element))
                element.clear()
            await AsyncPDFCraft.from_engine(engine).extract_pdf(
                Path(directory) / 'book.pdf', target,
                ExtractionOptions(includes_furniture=False, on_body=callback),
            )
            self.assertEqual(len(calls), 3)
            with ZipFile(target) as archive:
                self.assertEqual(len(fromstring(archive.read('chapters/chapter_head.xml')).findall('./flow/text')), 3)

    def test_missing_pdf_callbacks_skip_xml_packaging_independently(self):
        from pdf_craft.extractor.chapter.stream import resolve_to_disk
        with tempfile.TemporaryDirectory() as directory:
            with patch('pdf_craft.extractor.chapter.stream._encode_flow', side_effect=AssertionError('unused body')), patch(
                'pdf_craft.extractor.chapter.stream._encode_reference', side_effect=AssertionError('unused note'),
            ):
                resolve_to_disk(lambda: [page(1, 'Text①.', '① Note.')], Path(directory) / 'content.sqlite')

    async def test_pdf_footnote_callback_is_detached_and_optional(self):
        received = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / 'ocr'
            pages.mkdir()
            async def callback(element):
                received.append(tostring(element))
                element.clear()
            live = LiveExtraction(root / 'live', pages, True, on_footnote=callback)
            try:
                for index in range(1, 3):
                    save_xml(encode_page(page(index, 'Text①.', '① Footnote.')), pages / f'page_{index}.xml')
                    await live.event(OCREvent(OCREventKind.START, index, 2))
                    await live.event(OCREvent(OCREventKind.COMPLETE, index, 2))
                await live.finish()
                self.assertEqual(len(received), 2)
                self.assertEqual(fromstring(received[0]).tag, 'ref')
                self.assertEqual(next(fromstring(received[0]).iter('fragment')).get('page_index'), '1')
            finally:
                await live.cancel()

    async def test_vendor_requests_start_while_later_pages_are_still_rendering(self):
        import threading
        from PIL import Image
        from pdf_craft import DeepSeekOCRVendorConfig
        from pdf_craft.pdf.ocr import OCR
        from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRRuntime
        from tests.test_vendor_ocr_concurrency import _Document
        started = threading.Event()
        class Document(_Document):
            def render_page(self, page_index, dpi):
                del dpi
                if page_index == 2 and not started.wait(2):
                    raise AssertionError('OCR waited for all pages to render')
                return Image.new('RGB', (12, 16), 'white')
        class Handler:
            def open(self, _path):
                return Document([])
        async def request(_runtime, _request, _started=None, *, request_headers=None):
            self.assertEqual(request_headers, {})
            started.set()
            if _started is not None:
                _started.set()
            return VendorOCRResponse({}, raw_text='', input_tokens=0, output_tokens=0)
        from typing import cast
        with tempfile.TemporaryDirectory() as directory, patch.object(VendorOCRRuntime, 'request', new=request):
            root = Path(directory)
            ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://example.invalid', api_key='unused', model='unused'), cast(Any, Handler()))
            events = [event async for event in ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(2)), pdf_path=root / 'book.pdf',
                asset_path=root / 'assets', ocr_path=root / 'ocr')]
            self.assertEqual(sum(event.kind == OCREventKind.COMPLETE for event in events), 3)

    async def test_async_translation_source_closes_on_cancellation(self):
        from pdf_craft.transformer.xml_translator.xml_translator.concurrency import run_async_parameters
        closed, entered = asyncio.Event(), asyncio.Event()
        async def source():
            try:
                yield 1
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def execute(value):
            entered.set()
            await asyncio.Event().wait()
            return value
        async def consume():
            async for _ in run_async_parameters(source(), execute, 2):
                pass
        task = asyncio.create_task(consume())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_unresolved_footnote_source_is_delivered_as_body_at_eof(self):
        received = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / 'ocr'
            pages.mkdir()
            live = LiveExtraction(root / 'live', pages, True, lambda node: received.append(tostring(node)))
            try:
                save_xml(encode_page(page(1, 'Body.', 'Unresolved note.')), pages / 'page_1.xml')
                await live.event(OCREvent(OCREventKind.START, 1, 1))
                await live.event(OCREvent(OCREventKind.COMPLETE, 1, 1))
                await live.finish()
                self.assertEqual([''.join(fromstring(node).itertext()) for node in received], ['Body.', 'Unresolved note.'])
            finally:
                await live.cancel()

    async def test_sparse_footnotes_publish_closed_content_before_eof(self):
        await self._assert_sparse_footnotes_stream_before_eof('① Closed footnote.', None, True)

    async def test_unreferenced_footnote_publishes_before_eof(self):
        await self._assert_sparse_footnotes_stream_before_eof('① Unreferenced footnote.', None, True,
                                                            body_mark=False)

    async def test_unmatched_printed_mark_does_not_wait_for_eof(self):
        await self._assert_sparse_footnotes_stream_before_eof(None, None, False)

    async def test_cross_page_footnote_waits_for_continuation_then_publishes(self):
        await self._assert_sparse_footnotes_stream_before_eof('① This continues', 'on the next page.', True)

    async def _assert_sparse_footnotes_stream_before_eof(self, first_note, continuation, expects_note, *, body_mark=True):
        from pdf_craft.extractor.chapter.stream import chapters_from_disk
        bodies, notes = [], []
        body_ready, note_ready = asyncio.Event(), asyncio.Event()

        async def body_callback(node):
            bodies.append(tostring(node))
            if len(bodies) >= 2:
                body_ready.set()

        async def note_callback(node):
            notes.append(tostring(node))
            note_ready.set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / 'ocr'
            pages.mkdir()
            live = LiveExtraction(root / 'live', pages, True, body_callback, note_callback)
            try:
                for index in range(1, 7):
                    note = first_note if index == 1 else continuation if index == 2 else None
                    body = 'Body①.' if body_mark else 'Body.'
                    source = page(index, body if index == 1 else 'Closed sentence.', note or '')
                    if note is None:
                        source.footnotes_layouts = []
                    save_xml(encode_page(source), pages / f'page_{index}.xml')
                    await live.event(OCREvent(OCREventKind.START, index, 6))
                    await live.event(OCREvent(OCREventKind.COMPLETE, index, 6))
                    if continuation is not None and index <= 2:
                        # A definition spanning these two pages cannot be
                        # delivered as complete before its continuation closes.
                        self.assertFalse(note_ready.is_set())
                await asyncio.wait_for(body_ready.wait(), timeout=2)
                if expects_note:
                    await asyncio.wait_for(note_ready.wait(), timeout=2)
                self.assertFalse(live.task.done())
                self.assertEqual(len(notes), int(expects_note))
                refs = list(fromstring(bodies[0]).iter('ref'))
                self.assertEqual(len(refs), int(expects_note and body_mark))
                if not expects_note and body_mark:
                    self.assertIn('①', ''.join(fromstring(bodies[0]).itertext()))
                elif continuation is not None:
                    definition = fromstring(notes[0])
                    self.assertEqual([fragment.get('page_index') for fragment in definition.iter('fragment')], ['1', '2'])
                    self.assertIn('on the next page.', ''.join(definition.itertext()))
                await live.finish()
                self.assertEqual(len(bodies), 6)
                toc = TocInfo([], [])
                analyses = _analyse_chapter_pages(pages, toc)[1]
                expected = [tostring(encode(chapter)) for chapter in _generate_chapters(pages, toc, analyses)]
                actual = [tostring(encode(chapter)) for chapter in chapters_from_disk(live.database, toc)]
                self.assertEqual(actual, expected)
            finally:
                await live.cancel()

    def test_footnote_progress_keeps_cross_page_tail_open(self):
        from pdf_craft.extractor.chapter.jointer import JoinedPageProgress
        from pdf_craft.extractor.chapter.stream import _joined
        sources = [page(1, note='① This continues'), page(2, note='on the next page.'), page(3)]
        sources[2].footnotes_layouts = []
        events = _joined(sources, True).execute_with_progress()
        self.assertEqual(next(events), JoinedPageProgress(closed_through=0, processed_through=1))
        self.assertEqual(next(events), JoinedPageProgress(closed_through=0, processed_through=2))
        from pdf_craft.extractor.chapter.chapter import TextFlowItem
        paragraph = next(events)
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual([fragment.page_index for fragment in paragraph.children], [1, 2])
        self.assertEqual(next(events), JoinedPageProgress(closed_through=3, processed_through=3))
        self.assertEqual(list(events), [])
