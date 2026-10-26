"""Deterministic barriers for committed-input readiness and independent PCEX work."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import gc
import sqlite3
import threading
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch
from typing import Any, cast
from xml.etree.ElementTree import fromstring

from pdf_craft import AsyncPDFCraft, ExtractionOptions, ProgressEventKind as Kind, UnitKind, UnitOutcome, XMLTranslator
from pdf_craft._progress import DiskUnits, OperationProgress
from pdf_craft.common import save_xml
from pdf_craft.document import ExtractionPaths
from pdf_craft.document.content import read_content, file_content, asset_contexts
from pdf_craft.error import PDFError
from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, StandaloneAsset, encode
from pdf_craft.extractor.metadata import FrontPages, extract_book_metadata_from_ocr
from pdf_craft.pdf import encode as encode_page
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.progress import OperationKind
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.pcex_schedule import pcex_schedule
from pdf_craft.transformer.ready_queue import ReadyQueue
from tests.test_metadata_callbacks import FakeOCR, engine, native_metadata
from tests.test_one_shot_submission import source_fixture, table_asset
from tests.test_stream_callbacks import page
from tests.test_translation_word_progress import translator, identity_fill
from tests.test_work_unit_progress import assert_lifecycle, unit_events


async def wait(event):
    await asyncio.wait_for(event.wait(), 4)


async def identity(_self, text):
    return text


class FrontInputTests(IsolatedAsyncioTestCase):
    async def test_reads_only_requested_committed_prefix_with_out_of_order_pages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            front = FrontPages.open(root, 8, None)
            first, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            class Model:
                calls = 0
                assertion = ''
                async def request(self, *, input):
                    self.calls += 1
                    if self.calls == 1:
                        first.set()
                        return '{"action":"read_more","page_count":2}'
                    second.set()
                    self_text = input[-1].message
                    self.assertion = self_text
                    await release.wait()
                    return '{"action":"complete","metadata":{}}'
            model = Model()
            async def commit(index, kind=OCREventKind.COMPLETE):
                save_xml(encode_page(page(index)), root / f'page_{index}.xml')
                front.event(OCREvent(kind, index, 8))
                await asyncio.sleep(0)
            task = asyncio.create_task(extract_book_metadata_from_ocr(root, cast(Any, model), front_pages=front))
            try:
                await commit(3)
                await commit(1)
                self.assertFalse(first.is_set())
                await commit(2, OCREventKind.FAILED)
                await wait(first)
                self.assertFalse(second.is_set())
                await commit(5)
                self.assertFalse(second.is_set())
                await commit(4)
                await wait(second)
                self.assertFalse(task.done())
                self.assertIn('index="4"', model.assertion)
                self.assertIn('index="5"', model.assertion)
                self.assertNotIn('index="6"', model.assertion)
            finally:
                release.set()
                await task

    async def test_short_selected_cached_and_eof_inputs_match_sorted_file_reader(self):
        for selected, cached, ended in (({4, 8}, [4, 8], False), (None, list(range(1, 13)), False),
                                         ({4, 8}, [2, 4, 8], False), ({4, 8}, [4], True)):
            with self.subTest(selected=selected, cached=cached), TemporaryDirectory() as directory:
                root = Path(directory)
                for index in cached:
                    save_xml(encode_page(page(index)), root / f'page_{index}.xml')
                front = FrontPages.open(root, 12, selected)
                if ended:
                    front.finish()
                values, available = await asyncio.wait_for(front.load(3), 1)
                self.assertEqual([value.index for value in values], cached[:3])
                self.assertEqual(available, min(12, len(cached)))

    async def test_unselected_failed_cache_is_ready_before_later_selected_pages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode_page(page(2)), root / 'page_2.xml')
            (root / 'page_2.failed').touch()
            front = FrontPages.open(root, 20, {4, 8, 12, 16, 20})
            requested, release = asyncio.Event(), asyncio.Event()
            messages = []
            class Model:
                async def request(self, *, input):
                    messages.append(input[-1].message)
                    requested.set()
                    await release.wait()
                    return '{"action":"complete","metadata":{}}'
            task = asyncio.create_task(extract_book_metadata_from_ocr(root, cast(Any, Model()), front_pages=front))
            try:
                for index in (4, 8):
                    save_xml(encode_page(page(index)), root / f'page_{index}.xml')
                    front.event(OCREvent(OCREventKind.COMPLETE, index, 20))
                # No EOF or terminal events for pages 12/16/20. The unselected
                # failed page will receive IGNORE only, never a replacement.
                await wait(requested)
                self.assertFalse(front.ended)
                self.assertFalse(task.done())
                for index in (2, 4, 8):
                    self.assertIn(f'index="{index}"', messages[0])
                self.assertNotIn('index="12"', messages[0])
            finally:
                release.set()
                if not requested.is_set():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_partial_failed_cache_waits_for_terminal_replacement(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, 4):
                save_xml(encode_page(page(index)), root / f'page_{index}.xml')
            (root / 'page_2.failed').touch()
            front = FrontPages.open(root, 3, None)
            task = asyncio.create_task(front.load(3))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            front.event(OCREvent(OCREventKind.COMPLETE, 2, 3))
            values, count = await asyncio.wait_for(task, 1)
            self.assertEqual([value.index for value in values], [1, 2, 3])
            self.assertEqual(count, 3)

    async def test_async_handler_reports_native_metadata_before_page_materialization(self):
        from pdf_craft import PDFOptions
        from tests.test_async_api import _AsyncDocument, _AsyncHandler
        events, notifications, sequence = [], [], []
        class Document(_AsyncDocument):
            async def metadata(self):
                sequence.append('read')
                return native_metadata()
            async def render_page(self, page_index, dpi):
                sequence.append('render')
                return await super().render_page(page_index, dpi)
        craft = AsyncPDFCraft(PDFOptions(pdf_handler=_AsyncHandler(Document())))
        def callback(content):
            notifications.append(content)
            sequence.append('notify')
        with TemporaryDirectory() as directory:
            root = Path(directory)
            options = ExtractionOptions(includes_furniture=False, on_metadata=callback)
            from pdf_craft.extractor.progress import ExtractionProgress, _active
            progress = ExtractionProgress(events.append, root / 'units.sqlite', 2, None)
            token = _active.set(progress)
            try:
                await progress.start({UnitKind.METADATA: 1})
                async with craft._pdf_extractor(root / 'source.pdf', options) as extractor:
                    self.assertEqual(extractor._transform._native_metadata_result.title, 'Native title')
                await progress.finish()
            finally:
                _active.reset(token)
        self.assertEqual(sequence, ['read', 'notify', 'render', 'render'])
        self.assertEqual(len(notifications), 1)
        metadata_events = [event for event in unit_events(events, Kind.COMPLETED) if event.unit_kind == UnitKind.METADATA]
        self.assertEqual(len(metadata_events), 1)

    async def test_public_metadata_starts_while_later_local_ocr_is_blocked(self):
        first, second = asyncio.Event(), asyncio.Event()
        next_pages, last_page = threading.Event(), threading.Event()
        class OCR(FakeOCR):
            last_page_pixel_sizes = {index: (100, 100) for index in range(1, 7)}
            def _get_pdf_handler(self):
                return Mock(open=Mock(return_value=Mock(pages_count=6)))
            def recognize(self, **kwargs):
                root = kwargs['ocr_path']
                root.mkdir(parents=True, exist_ok=True)
                for index in range(1, 7):
                    if index == 4 and not next_pages.wait(4):
                        raise AssertionError('metadata did not start before later OCR')
                    if index == 6 and not last_page.wait(4):
                        raise AssertionError('metadata read_more waited for unrequested OCR')
                    yield OCREvent(OCREventKind.START, index, 6)
                    save_xml(encode_page(page(index)), root / f'page_{index}.xml')
                    yield OCREvent(OCREventKind.COMPLETE, index, 6)
        calls = 0
        async def request(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                first.set()
                return '{"action":"read_more","page_count":2}'
            second.set()
            return '{"action":"complete","metadata":{}}'
        with TemporaryDirectory() as directory, patch('pdf_craft.extractor.metadata._request_llm', request):
            root = Path(directory)
            events = []
            task = asyncio.create_task(AsyncPDFCraft.from_engine(engine(OCR([], native_metadata()))).extract_pdf(
                root / 'source.pdf', root / 'output.pcex', ExtractionOptions(
                    includes_furniture=False, extract_book_metadata=True, metadata_llm=translator()._translation_llm,
                    on_extraction_event=events.append)))
            try:
                await wait(first)
                self.assertFalse(second.is_set())
                next_pages.set()
                await wait(second)
                self.assertFalse(task.done())
            finally:
                next_pages.set()
                last_page.set()
                await task
            assert_lifecycle(self, events)

    async def test_native_metadata_counts_and_artifact_do_not_depend_on_callbacks(self):
        for native in (native_metadata(), PDFError('native field damage')):
            documents = []
            for contents in (False, True):
                with self.subTest(contents=contents, failure=isinstance(native, Exception)), TemporaryDirectory() as directory:
                    root, events, sequence, snapshots = Path(directory), [], [], []
                    def event(value):
                        events.append(value)
                        if value.unit_kind == UnitKind.METADATA and value.kind == Kind.COMPLETED:
                            sequence.append('metadata-completed')
                    result = await AsyncPDFCraft.from_engine(engine(FakeOCR(sequence, native))).extract_pdf(
                        root / 'source.pdf', root / 'output.pcex', ExtractionOptions(
                            includes_furniture=False, on_extraction_event=event,
                            on_metadata=snapshots.append if contents else None), error_policy=True)
                    documents.append(result._document_metadata())
                    self.assertLess(sequence.index('metadata-completed'), sequence.index('ocr'))
                    assert_lifecycle(self, events)
                    self.assertEqual(events[0].unit_counts[UnitKind.METADATA], 1)
                    ends = [event for event in unit_events(events, Kind.COMPLETED) if event.unit_kind == UnitKind.METADATA]
                    self.assertEqual(len(ends), 1)
                    self.assertEqual(ends[0].outcome, UnitOutcome.FALLBACK if isinstance(native, Exception) else UnitOutcome.PROCESSED)
                    self.assertEqual(len(snapshots), int(contents))
            self.assertEqual(documents[0], documents[1])


class PCEXDependencyTests(IsolatedAsyncioTestCase):
    async def test_blocked_metadata_and_body_leave_notes_assets_and_furniture_ready(self):
        for rendering in (False, True):
            with self.subTest(rendering=rendering), TemporaryDirectory() as directory:
                root, events = Path(directory), []
                source = source_fixture(root / 'source')
                furniture(root / 'source')
                metadata_started, body_started, note_delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()
                asset_started, furniture_started = asyncio.Event(), asyncio.Event()
                release = asyncio.Event()
                delivered = []
                async def metadata(_self, _fields):
                    metadata_started.set()
                    await release.wait()
                    return {}
                async def request(_self, text):
                    if 'First' in text:
                        body_started.set()
                        await release.wait()
                    if 'Caption' in text or 'Grid' in text:
                        asset_started.set()
                    if 'Independent footer' in text:
                        furniture_started.set()
                    return text
                async def footnote(content):
                    delivered.append(content.text)
                    note_delivered.set()
                with patch.object(NarrativeXMLTransformer, '_translate_metadata_fields', metadata), patch.object(
                        XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                    if rendering:
                        # Direct content_view is also the PDF writeback input;
                        # both run through the same ready/pending scheduler.
                        from pdf_craft.document.stream_view import content_view
                        async def render():
                            async with content_view(source, translator=translator(), window=4, with_furniture=True,
                                                    on_footnote=footnote, on_translation_event=events.append) as (_, _, _, items):
                                async for _ in items:
                                    pass
                        task = asyncio.create_task(render())
                    else:
                        task = asyncio.create_task(AsyncPDFCraft().translate_extraction(source, root / 'output.pcex',
                            translator(), target_language='fr', window=4, with_furniture=True,
                            on_footnote=footnote, on_translation_event=events.append))
                    try:
                        await wait(metadata_started)
                        await wait(body_started)
                        await wait(note_delivered)
                        await wait(asset_started)
                        await wait(furniture_started)
                        self.assertFalse(task.done())
                    finally:
                        release.set()
                        await task
                self.assertEqual(len(delivered), 1)
                assert_lifecycle(self, events)
                self.assertEqual(events[-1].completed_words, events[0].total_words)

    async def test_title_releases_only_its_dependants_and_fallback_never_hangs(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback), TemporaryDirectory() as directory:
                root = Path(directory)
                source_fixture(root / 'source')
                furniture(root / 'source')
                heading_entered, unrelated_entered = asyncio.Event(), asyncio.Event()
                release_title, release_other = asyncio.Event(), asyncio.Event()
                heading_calls = 0
                class Model:
                    async def transform(self, chapter):
                        nonlocal heading_calls
                        target = deepcopy(chapter)
                        if target.flow_items and isinstance(target.flow_items[0], TextFlowItem):
                            item = target.flow_items[0]
                            if item.role == 'heading':
                                heading_calls += 1
                                heading_entered.set()
                                await release_title.wait()
                                item.children[0].content = ['Translated title']
                                setattr(item, '_translation_missing', fallback)
                            elif 'First' in str(item):
                                await release_other.wait()
                        return target
                    def _furniture_transformer(self):
                        class Furniture:
                            async def transform_position(self, position):
                                unrelated_entered.set()
                                return position.content
                        return Furniture()
                paths = ExtractionPaths.at(root / 'source')
                async with pcex_schedule(read_content(paths.chapters), Model(), 3, paths=paths, root=root,
                                         with_furniture=True) as schedule:
                    try:
                        await wait(heading_entered)
                        await wait(unrelated_entered)
                        self.assertEqual(schedule.queue.run('SELECT resolved FROM titles WHERE id=1'), [(0,)])
                        release_title.set()
                        # The dependent reuse is not an LLM call. Observe the
                        # header barrier while unrelated body work stays blocked.
                        await asyncio.wait_for(schedule.header(), 4)
                        self.assertFalse(release_other.is_set())
                        xml = fromstring((root / 'furnitures.xml').read_bytes())
                        expected = 'Original title' if fallback else 'Translated title'
                        position = xml.find('patterns/pattern/position')
                        assert position is not None
                        self.assertEqual(position.text, expected)
                    finally:
                        release_title.set()
                        release_other.set()
                        async for _ in schedule.results([], []):
                            pass
                self.assertEqual(heading_calls, 1)

    async def test_window_is_shared_and_slow_ordered_head_does_not_pin_slots(self):
        active = maximum = 0
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        class Model:
            async def transform(self, chapter):
                nonlocal active, maximum
                text = str(chapter)
                active += 1
                maximum = max(maximum, active)
                try:
                    if 'First' in text:
                        entered.set()
                        await release.wait()
                    seen.append(text)
                    return chapter
                finally:
                    active -= 1
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            async with pcex_schedule(read_content(root / 'chapters'), Model(), 2) as schedule:
                try:
                    await wait(entered)
                    # Reading completion of the last note is a barrier, not a
                    # timing assertion: every earlier ready job can use slot 2.
                    while not any('Note' in value for value in seen):
                        schedule.changed.clear()
                        if any('Note' in value for value in seen):
                            break
                        await asyncio.wait_for(schedule.changed.wait(), 4)
                    self.assertEqual(active, 1)
                    self.assertLessEqual(maximum, 2)
                    assert schedule.runner is not None
                    self.assertFalse(schedule.runner.done())
                finally:
                    release.set()
                    items = [item async for item in schedule.results([], [])]
            self.assertEqual([(item.filename, item.index) for item in items],
                             [(item.filename, item.index) for item in file_content(root / 'chapters/chapter_1.xml')])

    async def test_body_and_note_callbacks_each_reorder_without_waiting_for_other_channel(self):
        from pdf_craft.extractor.chapter.chapter import Reference
        from pdf_craft.renderer.paragraph import paragraph_notifications
        from pdf_craft.document.access import page_geometries
        body_blocked, note_done = asyncio.Event(), asyncio.Event()
        release = asyncio.Event()
        bodies, notes = [], []
        class Model:
            async def transform(self, chapter):
                if chapter.flow_items and 'Body 0' in str(chapter):
                    body_blocked.set()
                    await release.wait()
                return chapter
        async def note_callback(content):
            notes.append(content.text)
            if len(notes) == 3:
                note_done.set()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root / 'source')
            def text(index, value):
                return TextFlowItem('body', 0, [SourceTextFragment(1, index, (0, 0, 10, 10), [value])])
            chapter = Chapter(1, 0, [text(index, f'Body {index}') for index in range(3)],
                              [Reference(index + 1, 1, str(index + 1), [text(index + 3, f'Note {index}')]) for index in range(3)])
            save_xml(encode(chapter), root / 'source/chapters/chapter_1.xml')
            async with paragraph_notifications(page_geometries({1: (100, 100)}, 300), root / 'source/assets',
                                               {1: 1, 2: 2, 3: 3}, bodies.append, note_callback) as notify:
                async with pcex_schedule(read_content(root / 'source/chapters'), Model(), 2, notifier=notify) as schedule:
                    try:
                        await wait(body_blocked)
                        await wait(note_done)
                        self.assertEqual(bodies, [])
                        self.assertEqual(notes, ['Note 0', 'Note 1', 'Note 2'])
                    finally:
                        release.set()
                        async for _ in schedule.results([], []):
                            pass
            self.assertEqual([content.text for content in bodies], ['Body 0', 'Body 1', 'Body 2'])

    async def test_original_context_stream_matches_existing_scope_and_never_counts_context(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_fixture(root)
            asset = table_asset()
            heading = TextFlowItem('heading', 1, [SourceTextFragment(1, 20, (0, 0, 20, 20), ['H' * 490])])
            following = TextFlowItem('body', 0, [SourceTextFragment(1, 21, (0, 20, 20, 40), ['F' * 600])])
            save_xml(encode(Chapter(1, 0, [heading, *[StandaloneAsset(deepcopy(asset)) for _ in range(20)], following])), root / 'chapters/chapter_1.xml')
            expected = asset_contexts(root / 'chapters/chapter_1.xml')
            actual = [item async for item in read_content(root / 'chapters')]
            for item in actual:
                self.assertEqual(item.asset_context, expected.get(item.index, ''))
                self.assertLessEqual(len(item.asset_context), 480)
            with patch.object(XMLTranslator, '_translate_text_async', identity), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                result = await AsyncPDFCraft().translate_extraction(source, root / 'output.pcex', translator(), target_language='fr', window=3)
            with result._materialize() as paths:
                for file in paths.chapters.glob('*.xml'):
                    self.assertNotIn('translation-context', file.read_text())


class TerminationTests(IsolatedAsyncioTestCase):
    async def test_early_content_view_exit_cancels_blocked_siblings_without_release(self):
        from pdf_craft.document.stream_view import content_view
        for close_stream in (False, True):
            with self.subTest(close_stream=close_stream), TemporaryDirectory() as directory:
                root = Path(directory)
                source = source_fixture(root / 'source')
                blocked, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
                events = []
                async def request(_self, text):
                    if 'First' in text:
                        blocked.set()
                        try:
                            await release.wait()
                        finally:
                            cancelled.set()
                    return text
                async def consume():
                    async with content_view(source, translator=translator(), window=2,
                                            with_anchored=False, on_translation_event=events.append) as (_, _, _, items):
                        await wait(blocked)
                        await anext(items)
                        if close_stream:
                            await items.aclose()
                        # Both normal context exit and explicit stream closure
                        # must stop unfinished work, without releasing the LLM.
                with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                        XMLTranslator, '_request_and_submit_async', identity_fill):
                    task = asyncio.create_task(consume())
                    try:
                        await asyncio.wait_for(asyncio.shield(task), 4)
                        self.assertTrue(cancelled.is_set())
                        self.assertFalse(release.is_set())
                        self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))
                    finally:
                        task.cancel()
                        release.set()
                        await asyncio.gather(task, return_exceptions=True)


    async def test_failure_cancel_and_callback_error_settle_all_active_jobs(self):
        from pdf_craft.concurrency import NonContinuableError
        for mode in ('fatal', 'cancel', 'callback'):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                source = source_fixture(root / 'source')
                entered, finish, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
                events, active = [], 0
                original = NarrativeXMLTransformer.transform
                failure = NonContinuableError('provider stopped')
                async def transform(self, chapter, **kwargs):
                    nonlocal active
                    if chapter.flow_items and isinstance(chapter.flow_items[0], TextFlowItem) and chapter.flow_items[0].role == 'heading':
                        active += 1
                        entered.set()
                        try:
                            await release.wait()
                        finally:
                            active -= 1
                            finish.set()
                    elif mode == 'fatal':
                        await entered.wait()
                        raise failure
                    return await original(self, chapter, **kwargs)
                async def callback(_content):
                    await entered.wait()
                    raise ValueError('callback rejected result')
                with patch.object(NarrativeXMLTransformer, 'transform', transform), patch.object(
                        XMLTranslator, '_translate_text_async', identity), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                    task = asyncio.create_task(AsyncPDFCraft().translate_extraction(source, root / 'output.pcex',
                        translator(), window=3, on_footnote=callback if mode == 'callback' else None,
                        on_translation_event=events.append))
                    try:
                        await wait(entered)
                        if mode == 'cancel':
                            task.cancel()
                        expected = asyncio.CancelledError if mode == 'cancel' else (ValueError if mode == 'callback' else NonContinuableError)
                        with self.assertRaises(expected) as caught:
                            await asyncio.wait_for(task, 4)
                        if mode == 'fatal':
                            self.assertIs(caught.exception, failure)
                        self.assertTrue(finish.is_set())
                        self.assertEqual(active, 0)
                        self.assertFalse((root / 'output.pcex').exists())
                        self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))
                    finally:
                        release.set()
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)

    async def test_metadata_callback_failure_cancels_later_ocr_and_retains_original_cause(self):
        later, stopped = threading.Event(), threading.Event()
        class OCR(FakeOCR):
            last_page_pixel_sizes = {index: (100, 100) for index in range(1, 5)}
            def _get_pdf_handler(self):
                return Mock(open=Mock(return_value=Mock(pages_count=4)))
            def recognize(self, **kwargs):
                root = kwargs['ocr_path']
                root.mkdir(parents=True, exist_ok=True)
                try:
                    for index in range(1, 5):
                        if index == 4:
                            later.set()
                            # The engine's cancellation bridge must end the
                            # worker, rather than abandon it with live files.
                            while not kwargs['aborted']():
                                stopped.wait(0.01)
                            return
                        yield OCREvent(OCREventKind.START, index, 4)
                        save_xml(encode_page(page(index)), root / f'page_{index}.xml')
                        yield OCREvent(OCREventKind.COMPLETE, index, 4)
                finally:
                    stopped.set()
        failure = ValueError('metadata callback failed')
        async def callback(_content):
            self.assertTrue(await asyncio.to_thread(later.wait, 3))
            raise failure
        async def request(*_args):
            return '{"action":"complete","metadata":{}}'
        with TemporaryDirectory() as directory, patch('pdf_craft.extractor.metadata._request_llm', request):
            root = Path(directory)
            with self.assertRaises(ValueError) as caught:
                await asyncio.wait_for(AsyncPDFCraft.from_engine(engine(OCR([], native_metadata()))).extract_pdf(
                    root / 'source.pdf', root / 'output.pcex', ExtractionOptions(
                        includes_furniture=False, extract_book_metadata=True, metadata_llm=translator()._translation_llm,
                        on_metadata=callback)), 4)
            self.assertIs(caught.exception, failure)
            self.assertTrue(stopped.is_set())
            self.assertFalse((root / 'output.pcex').exists())


class ConnectionTests(TestCase):
    def test_disk_units_and_queue_close_connections_even_when_sql_raises(self):
        connections = []
        connect = sqlite3.connect
        def track(*args, **kwargs):
            db = connect(*args, **kwargs)
            connections.append(db)
            return db
        with TemporaryDirectory() as directory, patch('sqlite3.connect', track):
            root = Path(directory)
            progress = OperationProgress(None, OperationKind.EXTRACTION)
            units = DiskUnits(root / 'units.sqlite', progress)
            from pdf_craft._progress import WorkUnit
            unit = WorkUnit(progress, 0, UnitKind.PAGE, None, 5)
            units.save(1, unit)
            loaded = units.load(1)
            assert loaded is not None
            self.assertEqual(loaded.id, 0)
            units.delete(1)
            self.assertIsNone(units.load(1))
            queue = ReadyQueue(root / 'queue.sqlite')
            with self.assertRaises(sqlite3.OperationalError):
                queue.run('SELECT * FROM missing_table')
            gc.collect()
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')


def furniture(root):
    save_xml(fromstring('''<furnitures><patterns>
      <pattern id="1" kind="universal"><position id="1" toc_id="1">Original title</position></pattern>
      <pattern id="2" kind="universal"><position id="1">Independent footer</position></pattern>
      </patterns><pages/></furnitures>'''), root / 'furnitures.xml')
