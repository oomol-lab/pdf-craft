"""Deterministic observation barriers; no hosted models are used."""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft import LLM, ConcurrentExecutor, FixedCapacity, SubmitKind, XMLTranslator, ProgressEventKind
from pdf_craft.pipeline.epub.translation.translator import translate
from tests.test_stream_callbacks import epub
from tests.test_translation_word_progress import identity_fill


class RealtimeObserversTests(IsolatedAsyncioTestCase):
    async def test_cross_page_chain_delivers_first_paragraph_before_last_translation(self):
        from reportlab.pdfgen.canvas import Canvas
        from pdf_craft import AsyncPDFCraft
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, encode
        from tests.extraction_helpers import make_extraction
        from tests.test_translation_word_progress import translator
        release, blocked, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        contents = []
        async def request(_self, text):
            if 'Last paragraph' in text:
                blocked.set()
                await release.wait()
            return text
        def body(content):
            contents.append(content)
            delivered.set()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.pdf'
            canvas = Canvas(str(source), pagesize=(200, 200))
            for _ in range(4):
                canvas.showPage()
            canvas.save()
            extraction = make_extraction(root/'extraction',
                page_pixel_sizes={i: (200, 200) for i in range(1, 5)}, render_dpi=72)
            texts = ['First paragraph', 'Middle paragraph', 'Last paragraph']
            chapter = Chapter(None, -1, [TextFlowItem('body', 0, [
                SourceTextFragment(i+1, 1, (10,110,180,160), [text + ' begins']),
                SourceTextFragment(i+2, 0, (10,20,180,70), [' and ends.']),
            ]) for i, text in enumerate(texts)])
            save_xml(encode(chapter), root/'extraction/chapters/chapter_head.xml')
            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                    XMLTranslator, '_request_and_submit_async', identity_fill):
                task = asyncio.create_task(AsyncPDFCraft().translate_pdf(source, extraction, root/'out.pdf',
                    translator(), window=4, on_body=body, error_policy=False))
                try:
                    await asyncio.wait_for(blocked.wait(), 3)
                    await asyncio.wait_for(delivered.wait(), 10)
                    self.assertIn('First paragraph', contents[0].text)
                    self.assertEqual(len(contents), 1)
                    self.assertEqual([r.page.page_index for r in contents[0].regions], [1, 2])
                    self.assertTrue(any(r.lines for r in contents[0].regions))
                finally:
                    release.set()
                    await asyncio.wait_for(task, 20)
            self.assertEqual(len(contents), 3)

    async def test_unrefined_page_completion_does_not_wait_for_earlier_ocr(self):
        from pdf_craft.common import save_xml
        from pdf_craft.pdf import encode
        from pdf_craft.pdf.ocr import OCREvent, OCREventKind
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from tests.test_stream_callbacks import page
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(page(2)), root/'page_2.xml')
            live = LiveExtraction(root/'live', root, False)
            try:
                with patch('pdf_craft.extractor.progress.page_stage') as stage:
                    await live.event(OCREvent(OCREventKind.START, 1, 2))
                    await live.event(OCREvent(OCREventKind.START, 2, 2))
                    await live.event(OCREvent(OCREventKind.COMPLETE, 2, 2))
                    stage.assert_awaited_once_with(2, 7)
                    self.assertEqual(live.position, 0)
            finally:
                await live.cancel()

    async def test_epub_completed_fragment_precedes_later_group(self):
        blocked, release = asyncio.Event(), asyncio.Event()
        events, contents, calls = [], [], []
        async def request(_self, text):
            calls.append(text)
            if len(calls) == 2:
                blocked.set()
                await release.wait()
            return text
        with TemporaryDirectory() as directory:
            root = Path(directory)
            epub(root/'source.epub')
            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                    XMLTranslator, '_request_and_submit_async', identity_fill):
                task = asyncio.create_task(translate(root/'source.epub', root/'output.epub', 'fr', SubmitKind.REPLACE,
                    llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                    executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=30, window=1,
                    on_body=contents.append, on_translation_event=events.append))
                try:
                    await asyncio.wait_for(blocked.wait(), 2)
                    # Allow the independent delivery queue to run, without
                    # unblocking any provider work.
                    for _ in range(100):
                        if contents:
                            break
                        await asyncio.sleep(.001)
                    self.assertTrue(any(e.completed_words for e in events if e.kind == ProgressEventKind.PROGRESS))
                    self.assertTrue(contents, 'accepted fragment waited for the rest of its chapter')
                    self.assertEqual(contents[0].original, 'Sentence 0.')
                finally:
                    release.set()
                    await asyncio.wait_for(task, 5)
                self.assertEqual([c.original for c in contents], [f'Sentence {i}.' for i in range(8)])

    async def test_pdf_note_precedes_unrelated_body_metadata_and_furniture(self):
        from reportlab.pdfgen.canvas import Canvas
        from pypdf import PdfReader
        from pdf_craft import AsyncPDFCraft, BookMeta
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, Reference, encode
        from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
        from tests.extraction_helpers import make_extraction
        from tests.test_translation_word_progress import translator
        release, metadata_entered, body_entered, furniture_entered, note_delivered = (asyncio.Event() for _ in range(5))
        bodies, notes = [], []
        async def metadata(_self, _fields):
            metadata_entered.set()
            await release.wait()
            return {'title': 'Translated title'}
        async def request(_self, text):
            if 'Blocked body' in text:
                body_entered.set()
                await release.wait()
            if 'Blocked footer' in text:
                furniture_entered.set()
                await release.wait()
            return text
        def note(content):
            notes.append(content)
            note_delivered.set()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.pdf'
            canvas = Canvas(str(source), pagesize=(200, 200))
            for i in range(3):
                canvas.drawString(10, 100, f'Original page {i+1}')
                canvas.showPage()
            canvas.save()
            extraction = make_extraction(root/'extraction', page_pixel_sizes={i:(200,200) for i in (1,2,3)},
                                         render_dpi=72, book_meta=BookMeta(title='Original title'))
            chapter = Chapter(None, -1, [
                TextFlowItem('body', 0, [SourceTextFragment(1, 0, (10,20,180,60), ['Blocked body.'])]),
                TextFlowItem('body', 0, [SourceTextFragment(3, 0, (10,20,180,60), ['Ready body.'])]),
            ], [Reference(2, 1, '1', [TextFlowItem('body', 0, [
                SourceTextFragment(2, 0, (10,100,180,140), ['Independent footnote.'])])])])
            save_xml(encode(chapter), root/'extraction/chapters/chapter_head.xml')
            (root/'extraction/furnitures.xml').write_text('<furnitures><patterns/><pages>'
                '<page index="3"><section det="10,170,180,190">Blocked footer</section></page>'
                '</pages></furnitures>')
            with patch.object(NarrativeXMLTransformer, '_translate_metadata_fields', metadata), patch.object(
                    XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                task = asyncio.create_task(AsyncPDFCraft().translate_pdf(source, extraction, root/'output.pdf',
                    translator(), window=6, with_furniture=True, on_body=bodies.append, on_footnote=note, error_policy=False))
                try:
                    await asyncio.wait_for(metadata_entered.wait(), 3)
                    await asyncio.wait_for(body_entered.wait(), 3)
                    await asyncio.wait_for(furniture_entered.wait(), 3)
                    await asyncio.wait_for(note_delivered.wait(), 15)
                    self.assertFalse(task.done())
                    self.assertFalse(bodies)
                    self.assertTrue(notes[0].regions[0].lines, 'callback must contain actual final layout')
                finally:
                    release.set()
                    await asyncio.wait_for(task, 20)
            self.assertEqual(len(notes), 1)
            self.assertEqual(len(bodies), 2)
            result = PdfReader(root/'output.pdf')
            self.assertEqual(len(result.pages), 3)
            assert result.metadata is not None
            self.assertEqual(result.metadata.title, 'Translated title')
            texts = [' '.join(page.extract_text().split()) for page in result.pages]
            self.assertIn('Blocked body', texts[0])
            self.assertIn('Independent footnote', texts[1])

    async def test_shared_furniture_template_releases_only_its_pages(self):
        from types import SimpleNamespace
        from pdf_craft.common import read_xml
        from pdf_craft.transformer.ready_queue import ReadyQueue
        from pdf_craft.transformer.furniture_plan import FurniturePlan
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'furnitures.xml'
            path.write_text('<furnitures><patterns><pattern id="1" kind="header">'
                '<position id="1">Shared</position></pattern><pattern id="2" kind="header">'
                '<position id="1">Unrelated</position></pattern></patterns><pages>'
                '<page index="1"><section det="1,1,90,10"><association pattern_id="1" position_id="1"/></section></page>'
                '<page index="2"><section det="1,1,90,10"><association pattern_id="1" position_id="1"/></section></page>'
                '<page index="3"><section det="1,1,90,10"><association pattern_id="2" position_id="1"/></section></page>'
                '</pages></furnitures>')
            queue = ReadyQueue(root/'work.sqlite')
            plan = FurniturePlan(SimpleNamespace(furnitures=path, toc=root/'toc.xml'), root/'out', queue)
            self.assertIsNone(plan.page_snapshot(1))
            job = queue.take(['furniture'])
            assert job is not None
            position = read_xml(path).find('patterns/pattern/position')
            assert position is not None
            position.text = 'Translated shared'
            queue.finish(job[0], ('position', '1', '1', position, 'translated'))
            for index in (1, 2):
                snapshot = plan.page_snapshot(index)
                assert snapshot is not None
                self.assertEqual(snapshot[0].findtext('patterns/pattern/position'), 'Translated shared')
            self.assertIsNone(plan.page_snapshot(3))
            self.assertEqual(len(queue.run('SELECT id FROM jobs WHERE state="done"')), 1)

    async def test_pdf_ready_windows_keep_cross_page_dependencies_and_skip_unrelated_work(self):
        from pdf_craft.pipeline.pdf.feed import ReplacementFeed, dependency_windows, write_record
        spans = [(0, 1, 2), (1, 2, 3), (2, 4, 4)]
        windows = dependency_windows(spans)
        self.assertEqual(windows, ((0, 1), (2,)))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_record(root/'2', 'ready fourth page')
            feed = ReplacementFeed(root, 3, windows)
            self.assertEqual(await asyncio.wait_for(asyncio.to_thread(feed.first), 1), 'ready fourth page')
            # A cancelled/failed producer must wake the process-side wait.
            write_record(root/'error', 'producer stopped')
            with self.assertRaisesRegex(RuntimeError, 'producer stopped'):
                await asyncio.to_thread(lambda: list(feed.ready_batches()))

    async def test_epub_atomic_owner_is_not_notified_before_later_piece_fails(self):
        from xml.etree.ElementTree import fromstring, Element
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        from tests.test_translation_word_progress import translator
        blocked, release = asyncio.Event(), asyncio.Event()
        received = []
        async def mappings(_self, inline_segments, **_kwargs):
            result = []
            for segment in inline_segments:
                text = ''.join(part.text for part in segment)
                if 'After' in text and not getattr(segment, '_translation_context', False):
                    blocked.set()
                    await release.wait()
                    result.append(None)
                else:
                    translated = Element('p')
                    translated.text = text
                    result.append((segment.parent, list(search_text_segments(translated))))
            return result
        async def tasks():
            yield TranslationTask(fromstring('<body><div>Before.<p>Inner.</p>After.</div><p>Safe.</p></body>'),
                                  SubmitKind.REPLACE, None)
        with patch.object(XMLTranslator, '_translate_inline_segments_async', mappings):
            operation = asyncio.create_task(translator()._translate_elements(tasks(), window=1,
                on_mapping=lambda value: received.append(''.join(t.text for t in value[0]))))
            try:
                await asyncio.wait_for(blocked.wait(), 2)
                self.assertEqual(received, [], 'atomic parent still has an unfinished piece')
            finally:
                release.set()
                await asyncio.wait_for(operation, 3)
            self.assertEqual(received, ['Safe.'])
