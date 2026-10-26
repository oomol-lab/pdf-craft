"""Review regressions: recovery boundaries, bounded admission, logical assets."""
# pylint: disable=protected-access
import asyncio
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import AsyncPDFCraft, ProgressEventKind as Kind, UnitKind, UnitOutcome, XMLTranslator, SubmitKind
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import Chapter, FlowItem, SourceAsset, SourceTextFragment, TextFlowItem, StandaloneAsset, encode
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.test_one_shot_submission import source_fixture
from tests.test_translation_word_progress import translator, identity_fill
from tests.test_work_unit_progress import assert_lifecycle, unit_events


async def source_response(_self, text):
    return text


def five_assets(root):
    extraction = source_fixture(root)
    from pdf_craft.common import read_xml
    from pdf_craft.extractor.chapter.chapter import decode
    chapter = decode(read_xml(root / 'chapters/chapter_1.xml'))
    body = chapter.flow_items[1]
    assert isinstance(body, TextFlowItem)
    prototype = body.children[2]
    assert isinstance(prototype, SourceAsset)
    content: list[FlowItem] = [TextFlowItem('body', 0, [SourceTextFragment(1, 0, (0, 0, 90, 10), ['Body survives'])])]
    for index in range(5):
        asset = deepcopy(prototype)
        asset.caption = [f'Caption {index} words']
        content.append(StandaloneAsset(asset))
    save_xml(encode(Chapter(1, 0, content)), root / 'chapters/chapter_1.xml')
    return extraction._validate()


class AssetProgressTests(IsolatedAsyncioTestCase):
    async def test_asset_decoder_recovery_matches_success_without_callback(self):
        for observe in (False, True):
            with self.subTest(observe=observe), TemporaryDirectory() as folder:
                root, events = Path(folder), []
                extraction = source_fixture(root / 'source')
                with patch.object(XMLTranslator, '_translate_text_async', source_response), patch.object(
                        XMLTranslator, '_request_and_submit_async', identity_fill), patch(
                        'pdf_craft.transformer.anchored_xml._decode_assets_by_slot',
                        side_effect=ValueError('asset decoding failure')):
                    result = await AsyncPDFCraft().translate_extraction(extraction, root / 'output.pcex',
                        translator(), target_language='fr', error_policy=True,
                        on_translation_event=events.append if observe else None)
                self.assertTrue(result._validate())
                if observe:
                    assert_lifecycle(self, events)
                    assets = [event for event in unit_events(events, Kind.COMPLETED) if event.unit_kind == UnitKind.ASSET]
                    self.assertEqual(len(assets), 2)
                    self.assertTrue(all(event.outcome == UnitOutcome.FALLBACK for event in assets))
                    self.assertEqual(events[-1].completed_words, events[0].total_words)

    async def test_raising_decoder_failure_or_cancellation_never_completes_assets(self):
        for failure in (ValueError('strict decoding failure'), asyncio.CancelledError('cancel decoding')):
            with self.subTest(failure=type(failure)), TemporaryDirectory() as folder:
                root, events = Path(folder), []
                extraction = source_fixture(root / 'source')
                with patch.object(XMLTranslator, '_translate_text_async', source_response), patch.object(
                        XMLTranslator, '_request_and_submit_async', identity_fill), patch(
                        'pdf_craft.transformer.anchored_xml._decode_assets_by_slot', side_effect=failure):
                    with self.assertRaises(type(failure)):
                        await AsyncPDFCraft().translate_extraction(extraction, root / 'output.pcex', translator(),
                            target_language='fr', error_policy=False, on_translation_event=events.append)
                self.assertTrue(any(event.unit_kind == UnitKind.ASSET for event in unit_events(events, Kind.STARTED)))
                self.assertFalse(any(event.unit_kind == UnitKind.ASSET for event in unit_events(events, Kind.COMPLETED)))
                self.assertFalse(any(event.unit_id is None and event.kind == Kind.COMPLETED for event in events))

    async def test_each_asset_count_and_word_progress_are_independent_of_batch_size(self):
        for batch_size in (1, 2, 4):
            with self.subTest(batch_size=batch_size), TemporaryDirectory() as folder:
                root, events = Path(folder), []
                extraction = five_assets(root / 'source')
                with patch('pdf_craft.transformer.anchored_translation._BATCH_SIZE', batch_size), patch.object(
                        XMLTranslator, '_translate_text_async', source_response), patch.object(
                        XMLTranslator, '_request_and_submit_async', identity_fill):
                    await AsyncPDFCraft().translate_extraction(extraction, root / 'output.pcex', translator(),
                        target_language='fr', on_translation_event=events.append)
                assert_lifecycle(self, events)
                self.assertEqual(events[0].unit_counts[UnitKind.ASSET], 5)
                starts = [event for event in unit_events(events, Kind.STARTED) if event.unit_kind == UnitKind.ASSET]
                self.assertEqual([event.total_words for event in starts], [3] * 5)
                asset_ids = {event.unit_id for event in starts}
                progress = [event for event in events if event.unit_id in asset_ids and event.kind == Kind.PROGRESS]
                self.assertTrue(all(0 <= event.unit_completed_words <= 3 for event in progress))
                self.assertEqual(sum(event.unit_completed_words for event in unit_events(events, Kind.COMPLETED)
                                     if event.unit_kind == UnitKind.ASSET), 15)
                self.assertEqual(events[0].total_words, 18)


class AdmissionTests(IsolatedAsyncioTestCase):
    async def test_synchronous_tasks_allocate_ids_only_when_admitted(self):
        events, starts_at_request = [], []
        runtime = translator()
        runtime._max_group_score = 1
        async def request(_self, text):
            starts_at_request.append(len(unit_events(events, Kind.STARTED)))
            return text
        tasks = [TranslationTask(fromstring(f'<body><p>Source {index}.</p></body>'), SubmitKind.REPLACE,
                                 index, item_kind=UnitKind.CONTENT, word_count=2) for index in range(20)]
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                XMLTranslator, '_request_and_submit_async', identity_fill):
            await runtime.translate_elements(tasks, window=1, on_translation_event=events.append)
        assert_lifecycle(self, events)
        self.assertEqual(starts_at_request[0], 1)
        self.assertEqual(starts_at_request, list(range(1, 21)))
        self.assertEqual(events[0].unit_counts[UnitKind.CONTENT], 20)

    async def test_one_pass_synchronous_input_is_not_collected_for_statistics(self):
        events = []
        produced = 0
        def tasks():
            nonlocal produced
            for index in range(20):
                produced += 1
                yield TranslationTask(fromstring(f'<body><p>Source {index}.</p></body>'), SubmitKind.REPLACE,
                                      index, item_kind=UnitKind.CONTENT, word_count=2)
        async def request(_self, text):
            if len(unit_events(events, Kind.STARTED)) == 1:
                self.assertLess(produced, 20)
            return text
        runtime = translator()
        runtime._max_group_score = 1
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                XMLTranslator, '_request_and_submit_async', identity_fill):
            await runtime.translate_elements(tasks(), window=1, unit_counts={UnitKind.CONTENT: 20}, total_words=40,
                                             on_translation_event=events.append)
        assert_lifecycle(self, events)

    async def test_async_tasks_allocate_ids_at_execution_not_producer_prefetch(self):
        events, starts_at_request = [], []
        async def tasks():
            for index in range(20):
                yield TranslationTask(fromstring(f'<body><p>Source {index}.</p></body>'), SubmitKind.REPLACE,
                                      index, item_kind=UnitKind.CONTENT, word_count=2)
        async def request(_self, text):
            starts_at_request.append(len(unit_events(events, Kind.STARTED)))
            return text
        runtime = translator()
        runtime._max_group_score = 1
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                XMLTranslator, '_request_and_submit_async', identity_fill):
            await runtime.translate_elements(tasks(), window=1, unit_counts={UnitKind.CONTENT: 20}, total_words=40,
                                             on_translation_event=events.append)
        assert_lifecycle(self, events)
        self.assertEqual(starts_at_request, list(range(1, 21)))

    async def test_opaque_batch_does_not_allocate_all_unit_ids_in_advance(self):
        from pdf_craft import NarrativeXMLTransformer
        from pdf_craft.transformer.progress import TranslationProgress, progress_context
        events = []
        class Opaque:
            target_language = 'fr'
            async def translate_element(self, task, **_kwargs):
                raise AssertionError('unexpected per-chapter fallback')
            async def translate_elements(self, tasks, **_kwargs):
                self_starts = unit_events(events, Kind.STARTED)
                if self_starts:
                    raise AssertionError('opaque execution invented unit admission facts')
                return [(task.element, task.payload) for task in tasks]
        progress = TranslationProgress(events.append)
        await progress.start({UnitKind.CONTENT: 20}, 40)
        chapters = ((Chapter(index, 0, [TextFlowItem('body', 0, [
            SourceTextFragment(1, 0, (0, 0, 10, 10), ['Source text'])])]), index) for index in range(20))
        with progress_context(progress):
            await NarrativeXMLTransformer(Opaque()).transform_many(chapters)
        await progress.finish()
        assert_lifecycle(self, events)
        active, maximum = 0, 0
        for event in events:
            if event.unit_id is not None and event.kind in (Kind.STARTED, Kind.COMPLETED):
                active += 1 if event.kind == Kind.STARTED else -1
                maximum = max(maximum, active)
        self.assertEqual(maximum, 1)
        self.assertEqual(Counter(event.unit_kind for event in unit_events(events, Kind.COMPLETED)), {UnitKind.CONTENT: 20})


class EmptyTaskTests(IsolatedAsyncioTestCase):
    async def test_consumed_empty_tasks_settle_without_changing_results(self):
        from xml.etree.ElementTree import tostring
        samples = {
            'empty': ('<body/>',),
            'mixed': ('<body/>', '<body><p>Alpha beta</p></body>', '<body><p> </p></body>',
                      '<body><p>Gamma</p></body>', '<body><section/></body>'),
        }
        for mode in ('reusable', 'iterator', 'async'):
            for name, xmls in samples.items():
                with self.subTest(mode=mode, sample=name):
                    events, results = [], []
                    for observe in (False, True):
                        tasks = [TranslationTask(fromstring(xml), SubmitKind.REPLACE, index)
                                 for index, xml in enumerate(xmls)]
                        async def stream(tasks=tasks):
                            for task in tasks:
                                yield task
                        supplied = stream() if mode == 'async' else iter(tasks) if mode == 'iterator' else tasks
                        kwargs: dict[str, Any] = dict(on_translation_event=events.append) if observe else {}
                        if observe and mode != 'reusable':
                            kwargs.update(unit_counts={UnitKind.CONTENT: len(xmls)},
                                          total_words=0 if name == 'empty' else 3)
                        with patch.object(XMLTranslator, '_translate_text_async', source_response), patch.object(
                                XMLTranslator, '_request_and_submit_async', identity_fill):
                            result = await translator().translate_elements(supplied, **kwargs)
                        results.append([(tostring(element), payload) for element, payload in result])
                    self.assertEqual(results[0], results[1])
                    self.assertEqual([payload for _, payload in results[1]], [] if name == 'empty' else [1, 3])
                    assert_lifecycle(self, events)
                    self.assertEqual(events[0].unit_counts[UnitKind.CONTENT], len(xmls))
                    skipped = [event for event in unit_events(events, Kind.COMPLETED) if event.outcome == UnitOutcome.SKIPPED]
                    self.assertEqual(len(skipped), 1 if name == 'empty' else 3)
                    self.assertEqual(events[-1].completed_words, 0 if name == 'empty' else 3)

    async def test_projection_removing_all_segments_settles_declared_words(self):
        events = []
        with patch.object(XMLTranslator, '_translate_text_async', side_effect=AssertionError('unexpected model call')):
            result = await translator().translate_elements([
                TranslationTask(fromstring('<body><p>Source words</p></body>'), SubmitKind.REPLACE, 0)],
                interrupt_source_text_segments=lambda _segments: (), on_translation_event=events.append)
        self.assertEqual(result, [])
        assert_lifecycle(self, events)
        self.assertEqual(events[0].total_words, 2)
        completed = unit_events(events, Kind.COMPLETED)
        self.assertEqual([(event.outcome, event.unit_completed_words) for event in completed], [(UnitOutcome.SKIPPED, 2)])


class EpubHeaderKindTests(IsolatedAsyncioTestCase):
    async def test_document_titles_and_navigation_share_unit_separate_from_book_metadata(self):
        from itertools import product
        from zipfile import ZipFile
        from tests.test_stream_callbacks import epub
        from pdf_craft.pipeline.epub.translation.translator import translate
        from pdf_craft import LLM, ConcurrentExecutor, FixedCapacity
        for has_title, has_metadata, has_navigation in product((False, True), repeat=3):
            with self.subTest(title=has_title, metadata=has_metadata, navigation=has_navigation), TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / 'input.epub'
                epub(source)
                with ZipFile(source) as archive:
                    members = {name: archive.read(name) for name in archive.namelist()}
                if has_title:
                    members['EPUB/a.xhtml'] = members['EPUB/a.xhtml'].replace(
                        b'<head/>', b'<head><title>Document title</title></head>')
                if has_metadata:
                    members['EPUB/book.opf'] = members['EPUB/book.opf'].replace(b'<metadata/>',
                        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book title</dc:title></metadata>')
                if has_navigation:
                    members['EPUB/toc.ncx'] = (
                        b'<ncx><navMap><navPoint id="a"><navLabel><text>Navigation title</text></navLabel>'
                        b'<content src="a.xhtml"/></navPoint></navMap></ncx>')
                with ZipFile(source, 'w') as archive:
                    for name, value in members.items():
                        archive.writestr(name, value)
                events = []
                with patch.object(XMLTranslator, '_translate_text_async', source_response), patch.object(
                        XMLTranslator, '_request_and_submit_async', identity_fill):
                    await translate(source, root / 'output.epub', 'fr', SubmitKind.REPLACE,
                        llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                        on_translation_event=events.append, executor=ConcurrentExecutor(FixedCapacity(2)))
                assert_lifecycle(self, events)
                self.assertEqual(events[0].unit_counts[UnitKind.METADATA], int(has_metadata))
                self.assertEqual(events[0].unit_counts[UnitKind.NAVIGATION], int(has_title or has_navigation))
                self.assertEqual(events[0].unit_counts[UnitKind.CONTENT], 1)
                starts = {event.unit_kind: event.total_words for event in unit_events(events, Kind.STARTED)}
                if has_metadata:
                    self.assertEqual(starts[UnitKind.METADATA], 2)
                if has_title or has_navigation:
                    self.assertEqual(starts[UnitKind.NAVIGATION], 2 * (int(has_title) + int(has_navigation)))
                self.assertEqual(events[0].total_words, 16 + 2 * sum((has_title, has_metadata, has_navigation)))
