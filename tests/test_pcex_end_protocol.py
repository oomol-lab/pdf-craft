"""Force queue reads to straddle producer EOF, without timing assumptions."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import AsyncPDFCraft, XMLTranslator, RenderMode
from pdf_craft.common import save_xml
from pdf_craft.document import ExtractionPaths
from pdf_craft.document.content import read_content
from pdf_craft.extractor.chapter.chapter import Chapter, Reference, SourceTextFragment, TextFlowItem, encode
from pdf_craft.runtime import IO_DOMAIN
from pdf_craft.transformer.pcex_schedule import PCEXSchedule, pcex_schedule
from tests.extraction_helpers import make_extraction
from tests.test_dependency_scheduling import furniture, identity
from tests.test_one_shot_submission import source_fixture
from tests.test_translation_word_progress import translator, identity_fill


class EndProtocolTests(IsolatedAsyncioTestCase):
    async def test_empty_admission_before_eof_cannot_release_pending_title(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), TemporaryDirectory() as directory:
                root = Path(directory)
                source_fixture(root / 'source')
                furniture(root / 'source')
                admit, scanned, heading, release = (asyncio.Event() for _ in range(4))
                scan = PCEXSchedule.scan
                run = IO_DOMAIN.run
                empty_reads = 0

                async def held_scan(schedule):
                    await admit.wait()
                    try:
                        await scan(schedule)
                    finally:
                        scanned.set()

                async def interleave(function, *args, **kwargs):
                    nonlocal empty_reads
                    result = await run(function, *args, **kwargs)
                    if getattr(function, '__name__', '') == 'take' and result is None and not admit.is_set():
                        empty_reads += 1
                        # First empty read follows admission of the independent
                        # footer. On the second, no jobs are running anymore.
                        if empty_reads == 2:
                            admit.set()
                            await scanned.wait()
                    return result

                class Model:
                    async def transform(self, chapter):
                        target = deepcopy(chapter)
                        if target.flow_items and isinstance(target.flow_items[0], TextFlowItem):
                            item = target.flow_items[0]
                            if item.role == 'heading':
                                heading.set()
                                await release.wait()
                                item.children[0].content = ['Translated title']
                                setattr(item, '_translation_missing', fallback)
                        return target

                    def _furniture_transformer(self):
                        class Furniture:
                            async def transform_position(self, position):
                                return position.content
                        return Furniture()

                with patch.object(PCEXSchedule, 'scan', held_scan), patch.object(IO_DOMAIN, 'run', interleave):
                    async with pcex_schedule(read_content(root / 'source/chapters'), Model(), 3,
                                             paths=ExtractionPaths.at(root / 'source'), root=root,
                                             with_furniture=True) as schedule:
                        try:
                            await asyncio.wait_for(heading.wait(), 5)
                            self.assertEqual(schedule.queue.run('SELECT resolved FROM titles WHERE id=1'), [(0,)])
                        finally:
                            release.set()
                            async for _ in schedule.results([], []):
                                pass
                xml = fromstring((root / 'furnitures.xml').read_bytes())
                self.assertEqual(xml.findtext('patterns/pattern/position'),
                                 'Original title' if fallback else 'Translated title')

    async def test_empty_callback_reads_before_eof_do_not_lose_final_pages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', page_pixel_sizes={i: (100, 100) for i in range(1, 4)})
            bodies, notes = [], []
            flow, references = [], []
            for page in range(1, 4):
                note = Reference(page, 1, str(page), [TextFlowItem('body', 0, [
                    SourceTextFragment(page, 1, (0, 60, 90, 90), [f'Note {page}'])])])
                flow.append(TextFlowItem('body', 0, [SourceTextFragment(page, 0, (0, 0, 90, 50), [f'Body {page}', note])]))
                references.append(note)
            save_xml(encode(Chapter(1, 0, flow, references)), root / 'source/chapters/chapter_1.xml')
            source._validate()
            empty_channels = set()
            admit, scanned = asyncio.Event(), asyncio.Event()
            scan, run = PCEXSchedule.scan, IO_DOMAIN.run

            async def held_scan(schedule):
                await admit.wait()
                try:
                    await scan(schedule)
                finally:
                    scanned.set()

            async def interleave(function, *args, **kwargs):
                result = await run(function, *args, **kwargs)
                if (args and isinstance(args[0], str) and 'channel=? AND notified=0' in args[0]
                        and not result and args[1][0] not in empty_channels):
                    empty_channels.add(args[1][0])
                    if len(empty_channels) == 2:
                        admit.set()
                    await scanned.wait()
                return result

            with patch.object(PCEXSchedule, 'scan', held_scan), patch.object(IO_DOMAIN, 'run', interleave), \
                    patch.object(XMLTranslator, '_translate_text_async', identity), \
                    patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                result = await asyncio.wait_for(AsyncPDFCraft().translate_extraction(
                    source, root / 'output.pcex', translator(), target_language='fr', window=3, translation_id='checked',
                    on_body=bodies.append, on_footnote=notes.append), 10)
            self.assertEqual(len(bodies), 3)
            self.assertEqual([note.text for note in notes], [f'Note {page}' for page in range(1, 4)])
            for page, body in enumerate(bodies, 1):
                self.assertIn(f'Body {page}', body.text)
            with result._materialize() as paths:
                xml = fromstring((paths.translations / 'checked/chapters/chapter_1.xml').read_bytes())
                self.assertEqual({int(n.get('page_index', '0')) for n in xml.iter('fragment')}, {1, 2, 3})
                text = ''.join(xml.itertext())
                for page in range(1, 4):
                    self.assertEqual(text.count(f'Body {page}'), 1)
                    self.assertEqual(text.count(f'Note {page}'), 1)

            stored_bodies, stored_notes = [], []
            await AsyncPDFCraft().render_markdown(
                root / 'output.pcex', root / 'output.md', mode=RenderMode.REPLACE,
                translation_id='checked', on_body=stored_bodies.append, on_footnote=stored_notes.append)
            for live, stored in ((bodies, stored_bodies), (notes, stored_notes)):
                self.assertEqual([p.text for p in live], [p.text for p in stored])
                self.assertEqual([p.regions for p in live], [p.regions for p in stored])

    async def test_failed_or_cancelled_scan_is_not_successful_eof(self):
        for error in (ValueError('broken input'), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                async def source():
                    raise error
                    yield  # pylint: disable=unreachable
                schedule = PCEXSchedule(source(), None, 1, None)
                with self.assertRaises(type(error)):
                    await schedule.scan()
                self.assertFalse(schedule.scanned)

    async def test_missing_title_is_released_only_after_all_input_settles(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root / 'source')
            furniture(root / 'source')
            # Keep the furniture binding, but remove its producer from the input.
            chapter_path = root / 'source/chapters/chapter_1.xml'
            chapter = fromstring(chapter_path.read_bytes())
            flow = chapter.find('flow')
            assert flow is not None
            flow.remove(flow[0])
            save_xml(chapter, chapter_path)

            class Model:
                async def transform(self, value):
                    return value

                def _furniture_transformer(self):
                    class Furniture:
                        async def transform_position(self, position):
                            return position.content
                    return Furniture()

            async def consume():
                async with pcex_schedule(read_content(root / 'source/chapters'), Model(), 2,
                                         paths=ExtractionPaths.at(root / 'source'), root=root,
                                         with_furniture=True) as schedule:
                    async for _ in schedule.results([], []):
                        pass
            await asyncio.wait_for(consume(), 5)
            xml = fromstring((root / 'furnitures.xml').read_bytes())
            self.assertEqual(xml.findtext('patterns/pattern/position'), 'Original title')
            coverage = (root / 'coverage.xml').read_text()
            self.assertIn('preserved', coverage)

    async def test_scanner_close_failure_wakes_owner_without_publishing_success(self):
        closing, release = asyncio.Event(), asyncio.Event()
        error = ValueError('source close failed')

        class Source:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                closing.set()
                await release.wait()
                raise error

        with TemporaryDirectory() as directory:
            from pdf_craft.transformer.ready_queue import ReadyQueue
            schedule = PCEXSchedule(Source(), None, 1, ReadyQueue(Path(directory) / 'jobs.sqlite'))
            owner = asyncio.create_task(schedule.run())
            try:
                await asyncio.wait_for(closing.wait(), 5)
                self.assertFalse(schedule.scanned)
                self.assertFalse(owner.done())
            finally:
                release.set()
                with self.assertRaises(ValueError) as caught:
                    await asyncio.wait_for(owner, 5)
                self.assertIs(caught.exception, error)
            self.assertFalse(schedule.scanned)
