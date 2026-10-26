"""Exercise real grouping/recovery with deterministic LLM boundaries."""
# pylint: disable=protected-access
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, FixedCapacity, LLM,
    SubmitKind, ProgressEventKind as Kind, UnitKind as Item, XMLTranslator,
)
from pdf_craft.document.stream_view import content_view
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, encode
from pdf_craft.transformer.word_count import count_xml_words
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from tests.extraction_helpers import make_extraction
from tests.test_stream_callbacks import epub
from pdf_craft.pipeline.epub.translation.translator import translate


def translator():
    llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
    return XMLTranslator(llm, llm, 'fr', None, 10, 30,
                         executor=ConcurrentExecutor(FixedCapacity(3)))


async def identity_fill(_self, hill_climbing, **_kwargs):
    error = hill_climbing.submit(hill_climbing.request_element())
    if error is not None:
        raise AssertionError(error)


class TranslationWordProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_accepted_round_reports_before_next_round(self):
        model_calls, events = [], []

        async def request(_self, text):
            if model_calls:
                self.assertTrue(any(event.kind == Kind.PROGRESS for event in events))
            model_calls.append(text)
            return 'accepted'

        element = fromstring('<body>' + ''.join(f'<p>hello {i} 中文</p>' for i in range(6)) + '</body>')
        total = count_xml_words(element)
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
            await translator().translate_element(TranslationTask(element, SubmitKind.REPLACE, None,
                item_kind=Item.CONTENT, item_id='chapter', word_count=total), on_translation_event=events.append)
        progress = [event for event in events if event.kind == Kind.PROGRESS]
        self.assertGreater(len(model_calls), 1)
        self.assertEqual(len([event for event in progress if event.stage == 0]), len(model_calls))
        self.assertEqual(events[0].kind, Kind.STARTED)
        self.assertEqual(events[0].total_words, total)
        self.assertEqual(events[-1].completed_words, total)
        self.assertEqual([event.completed_words for event in progress], sorted(event.completed_words for event in progress))
        self.assertTrue(all(event.unit_completed_words is not None for event in progress))

    async def test_final_fallback_counts_but_aborted_round_does_not_complete(self):
        async def fail(_self, _text):
            raise ValueError('final model failure')
        for allowed in (True, False):
            events = []
            element = fromstring('<body><p>source 中文</p></body>')
            task = TranslationTask(element, SubmitKind.REPLACE, None, item_kind=Item.CONTENT, item_id=1,
                                   word_count=count_xml_words(element))
            with patch.object(XMLTranslator, '_translate_text_async', fail):
                if allowed:
                    await translator().translate_element(task, on_translation_event=events.append,
                                                         error_policy=lambda _failure: True)
                    self.assertEqual(events[-1].completed_words, 3)
                    self.assertEqual(len([event for event in events if event.kind == Kind.PROGRESS and event.stage == 0]), 1)
                else:
                    with self.assertRaises(Exception):
                        await translator().translate_element(task, on_translation_event=events.append)
                    self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))

    async def test_start_before_metadata_and_title_preparation_and_no_title_recount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            extraction = make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            chapter = Chapter(1, 0, [
                TextFlowItem('heading', 1, [SourceTextFragment(1, 0, (0, 0, 80, 10), ['A heading'])]),
                TextFlowItem('body', 0, [SourceTextFragment(1, 1, (0, 10, 80, 90), ['Body 中文'])])])
            (source_root / 'chapters/chapter_1.xml').write_text(tostring(encode(chapter), encoding='unicode'))
            (source_root / 'furnitures.xml').write_text('<furnitures><patterns><pattern id="1" kind="header"><position id="1">Header text</position></pattern></patterns><pages><page index="1"><section det="0,0,10,10">Footer text</section></page></pages></furnitures>')
            import json
            manifest = source_root / 'manifest.json'
            data = json.loads(manifest.read_text())
            data['document']['title'] = 'Book title'
            manifest.write_text(json.dumps(data))
            events, calls = [], []
            entered, release = asyncio.Event(), asyncio.Event()

            async def request(_self, text):
                calls.append(text)
                self.assertEqual(events[0].kind, Kind.STARTED)
                self.assertEqual(events[0].total_words, 11)  # 2 metadata + 2 heading + 3 body + 4 furniture
                if len(calls) == 1:
                    entered.set()
                    await release.wait()
                return 'accepted'

            async def consume():
                async with content_view(extraction, translator=translator(), with_furniture=True,
                                        on_translation_event=events.append, with_anchored=False) as (_, _, _, items):
                    async for _ in items:
                        pass

            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                pending = asyncio.create_task(consume())
                try:
                    await asyncio.wait_for(entered.wait(), 3)
                    self.assertEqual(events[0].completed_words, 0)
                finally:
                    release.set()
                await pending
            self.assertEqual(sum('A heading' in text for text in calls), 1)
            progress = [event for event in events if event.kind == Kind.PROGRESS]
            self.assertEqual({event.unit_kind for event in progress}, {Item.CONTENT, Item.METADATA, Item.FURNITURE})
            self.assertEqual(events[-1].completed_words, 11)
            self.assertEqual(events[0].total_words, 11)

    async def test_epub_prescan_has_total_before_first_model_call(self):
        events, calls = [], []
        async def request(_self, text):
            calls.append(text)
            self.assertEqual(events[0].kind, Kind.STARTED)
            self.assertEqual(events[0].total_words, 16)
            return 'accepted'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub(root / 'source.epub')
            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                await translate(root / 'source.epub', root / 'output.epub', 'fr', SubmitKind.REPLACE,
                                llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                                executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=30,
                                on_translation_event=events.append)
            self.assertGreater(len(calls), 1)
            self.assertEqual(len([event for event in events if event.kind == Kind.PROGRESS and event.stage == 0]), len(calls))
            self.assertEqual(events[-1].completed_words, 16)

    async def test_concurrent_completion_is_monotonic_and_does_not_wait_for_slow_item(self):
        events = []
        entered, release, fast_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def request(_self, text):
            if 'slow' in text:
                entered.set()
                await release.wait()
            return 'accepted'
        async def record(event):
            events.append(event)
            if event.kind == Kind.PROGRESS and event.unit_id == 1:
                fast_done.set()
        runtime = translator()
        runtime._max_group_score = 1
        tasks = [TranslationTask(fromstring(f'<body><p>{name} source</p></body>'), SubmitKind.REPLACE,
                                 None, item_kind=Item.CONTENT, item_id=name, word_count=2)
                 for name in ('slow', 'fast')]
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
            pending = asyncio.create_task(runtime.translate_elements(tasks, window=2, on_translation_event=record))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(fast_done.wait(), 3)
                self.assertFalse(pending.done())
                fast = next(event for event in events if event.kind == Kind.PROGRESS and event.unit_id == 1)
                self.assertEqual(fast.completed_words, 2)
                self.assertEqual(events[0].total_words, 4)
            finally:
                release.set()
                await pending
        self.assertEqual(events[-1].completed_words, 4)

    async def test_fill_retry_never_advances_until_final_acceptance(self):
        from pdf_craft.llm.runtime import LLMContext
        from pdf_craft.transformer.xml_translator.xml import encode_friendly
        events, attempts, responses = [], [], []
        original_fill = XMLTranslator._request_and_submit_async
        async def fill(instance, hill_climbing, **kwargs):
            responses.append(encode_friendly(hill_climbing.request_element()))
            await original_fill(instance, hill_climbing=hill_climbing, **kwargs)
        async def request(_self, *_args, **kwargs):
            attempts.append(kwargs.get('retry_index'))
            self.assertFalse(any(event.kind == Kind.PROGRESS for event in events))
            return 'not XML' if len(attempts) == 1 else responses[-1]
        async def translated(_self, _text):
            return 'source text'
        with patch.object(XMLTranslator, '_translate_text_async', translated), patch.object(XMLTranslator, '_request_and_submit_async', fill), patch.object(LLMContext, 'request', request):
            await translator().translate_element(TranslationTask(fromstring('<body><p>source text</p></body>'),
                SubmitKind.REPLACE, None, item_kind=Item.CONTENT, item_id=1, word_count=2),
                on_translation_event=events.append)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len([event for event in events if event.kind == Kind.PROGRESS and event.stage == 0]), 1)
        self.assertEqual(events[-1].completed_words, 2)

    async def test_long_word_spanning_source_fragments_is_not_recounted(self):
        events = []
        runtime = translator()
        runtime._max_group_score = 1
        element = fromstring('<chapter><text><fragment>' + 'a' * 27 + '</fragment><fragment>' +
                             'b' * 13 + '</fragment></text></chapter>')
        async def request(_self, _text):
            return 'accepted'
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
            await runtime.translate_element(TranslationTask(element, SubmitKind.REPLACE, None,
                item_kind=Item.CONTENT, item_id=1, word_count=count_xml_words(element)),
                on_translation_event=events.append)
        self.assertEqual(events[0].total_words, 2)
        progress = [event.completed_words for event in events if event.kind == Kind.PROGRESS]
        self.assertEqual(progress, [2, 2, 2])

    async def test_enabled_asset_fields_are_counted_but_context_is_not(self):
        from tests.test_one_shot_submission import source_fixture
        async def request(_self, text):
            return text
        for enabled, total in ((False, 7), (True, 9)):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                extraction = source_fixture(root / 'source')
                events = []
                with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                    await AsyncPDFCraft().translate_extraction(extraction, root / 'output.pcex', translator(),
                        with_anchored=enabled, target_language='fr', on_translation_event=events.append)
                self.assertEqual(events[0].total_words, total)
                self.assertEqual(events[-1].completed_words, total)

    async def test_progress_callback_failure_is_not_recovered_as_model_failure(self):
        from pdf_craft import ErrorCallbackError
        events = []
        async def request(_self, _text):
            return 'accepted'
        def record(event):
            events.append(event)
            if event.kind == Kind.PROGRESS:
                raise ValueError('consumer failed')
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
            with self.assertRaises(ErrorCallbackError):
                await translator().translate_element(TranslationTask(fromstring('<body><p>hello</p></body>'),
                    SubmitKind.REPLACE, None, item_kind=Item.CONTENT, item_id=1, word_count=1),
                    on_translation_event=record, error_policy=lambda _error: True)
        self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))

    async def test_epub_totals_include_navigation_metadata_and_document_title(self):
        from zipfile import ZipFile
        async def request(_self, text):
            return text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.epub'
            epub(source)
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['EPUB/book.opf'] = members['EPUB/book.opf'].replace(b'<metadata/>',
                b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book title</dc:title></metadata>')
            members['EPUB/toc.ncx'] = b'<ncx><navMap><navPoint id="a"><navLabel><text>Navigation title</text></navLabel><content src="a.xhtml"/></navPoint></navMap></ncx>'
            members['EPUB/a.xhtml'] = members['EPUB/a.xhtml'].replace(b'<head/>', b'<head><title>Document title</title></head>')
            with ZipFile(source, 'w') as archive:
                for name, value in members.items():
                    archive.writestr(name, value)
            events = []
            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                await translate(source, root / 'output.epub', 'fr', SubmitKind.REPLACE,
                    llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                    executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=30,
                    on_translation_event=events.append)
            self.assertEqual(events[0].total_words, 22)
            self.assertEqual(events[-1].kind, Kind.COMPLETED)
            self.assertEqual(events[-1].completed_words, 22)
            self.assertEqual(events[0].unit_counts[Item.NAVIGATION], 1)
            self.assertEqual(events[0].unit_counts[Item.METADATA], 1)
            self.assertEqual(events[0].unit_counts[Item.CONTENT], 1)
            started = [event for event in events if event.kind == Kind.STARTED and event.unit_id is not None]
            self.assertEqual(sorted(event.total_words for event in started), [2, 4, 16])

    async def test_all_fallback_epub_advances_but_has_no_successful_complete(self):
        from pdf_craft import AllFallbackError
        async def fail(_self, _text):
            raise ValueError('final model failure')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            epub(root / 'source.epub')
            events = []
            with patch.object(XMLTranslator, '_translate_text_async', fail):
                with self.assertRaises(AllFallbackError):
                    await translate(root / 'source.epub', root / 'output.epub', 'fr', SubmitKind.REPLACE,
                        llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                        executor=ConcurrentExecutor(FixedCapacity(2)), max_group_tokens=30,
                        on_translation_event=events.append, error_policy=lambda _failure: True)
            progress = [event for event in events if event.kind == Kind.PROGRESS]
            self.assertEqual(progress[-1].completed_words, 16)
            self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))
            self.assertFalse((root / 'output.epub').exists())

    async def test_partial_stream_consumption_does_not_fabricate_complete(self):
        async def request(_self, text):
            return text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / 'source', page_pixel_sizes={1: (100, 100)})
            chapter = Chapter(1, 0, [TextFlowItem('body', 0, [
                SourceTextFragment(1, index, (0, index * 10, 80, index * 10 + 9), [f'Paragraph {index}'])])
                for index in range(4)])
            (root / 'source/chapters/chapter_1.xml').write_text(tostring(encode(chapter), encoding='unicode'))
            events = []
            with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
                async with content_view(extraction, translator=translator(), with_anchored=False,
                                        on_translation_event=events.append) as (_, _, _, items):
                    async for item in items:
                        if item.value is not None:
                            break
            self.assertFalse(any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events))

    async def test_formula_projections_keep_source_weights_per_round(self):
        from pdf_craft import NarrativeXMLTransformer
        from pdf_craft.expression import ExpressionKind
        from pdf_craft.extractor.chapter.chapter import DisplayFormula, InlineExpression, SourceAsset
        chapter = Chapter(1, 0, [
            TextFlowItem('body', 0, [SourceTextFragment(1, 0, (0, 0, 80, 10), [
                'Before ', InlineExpression(ExpressionKind.INLINE_DOLLAR, 'x^2'), ' after'])]),
            DisplayFormula(SourceAsset(1, 'formula', (0, 20, 80, 30),
                                       content=[InlineExpression(ExpressionKind.DISPLAY_DOUBLE_DOLLAR, 'a+b')])),
            TextFlowItem('body', 0, [SourceTextFragment(1, 2, (0, 40, 80, 50), ['After words'])])])
        runtime = translator()
        runtime._max_group_score = 1
        narrative = NarrativeXMLTransformer(runtime)
        events, calls = [], []
        async def request(_self, text):
            calls.append(text)
            return text
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(XMLTranslator, '_request_and_submit_async', identity_fill):
            await narrative.transform(chapter, on_translation_event=events.append)
        self.assertEqual(events[0].total_words, 8)
        self.assertEqual(events[-1].completed_words, 8)
        self.assertEqual(len([event for event in events if event.kind == Kind.PROGRESS and event.stage == 0]), len(calls))
