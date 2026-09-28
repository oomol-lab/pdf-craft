# pylint: disable=protected-access

import asyncio
import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import tostring

from pdf_craft import (
    ChapterExtractionTransformer,
    ChapterXMLTransformer,
    NonContinuableError,
    TranslationEventKind,
    TranslationItemKind,
)
from pdf_craft import PDFCraft
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, Chapter, TextFlowItem, encode
from tests.extraction_helpers import make_extraction


class TestTranslationEvents(unittest.TestCase):
    def test_direct_extraction_transform_does_not_claim_translation_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0, [SourceTextFragment(1, 1, (1, 1, 5, 5), ["source"])]
            )])
            (source_root / "chapters/chapter_head.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )

            class Identity:
                def transform(self, chapter):
                    return chapter

            events = []
            ChapterExtractionTransformer(Identity())._transform_blocking(
                source, root / "target.pcex", on_translation_event=events.append
            )
            self.assertEqual(events, [])

    def test_extraction_translation_reports_format_neutral_chapter_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (10, 10)})

            chapters = [
                Chapter(None, -1, [TextFlowItem(
                    "body", 0, [SourceTextFragment(1, 1, (1, 1, 5, 5), ["head"])]
                )]),
                Chapter(7, 1, [TextFlowItem(
                    "body", 0, [SourceTextFragment(1, 1, (1, 1, 5, 5), ["chapter"])]
                )]),
                Chapter(8, 1, []),
            ]
            for name, chapter in zip(("chapter_head.xml", "chapter_7.xml", "chapter_8.xml"), chapters):
                (source_root / "chapters" / name).write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    + tostring(encode(chapter), encoding="unicode")
                )

            class Identity:
                def transform(self, chapter):
                    return chapter

            events = []
            PDFCraft().translate_extraction(
                source, root / "target.pcex", Identity(), target_language="en",
                on_translation_event=events.append,
            )

            self.assertEqual(events[0].kind, TranslationEventKind.START)
            self.assertEqual(events[0].chapter_count, 2)
            self.assertFalse(events[0].has_toc)
            self.assertFalse(events[0].has_metadata)
            self.assertEqual(events[0].total_characters, len("headchapter"))
            item_starts = [event for event in events if event.kind == TranslationEventKind.ITEM_START]
            self.assertEqual(
                [(event.item_id, event.item_total_characters) for event in item_starts],
                [(7, len("chapter")), ("head", len("head"))],
            )
            self.assertEqual(
                [(event.kind, event.item_kind, event.item_id) for event in events
                 if event.kind in (TranslationEventKind.ITEM_START, TranslationEventKind.ITEM_COMPLETE)],
                [
                    (TranslationEventKind.ITEM_START, TranslationItemKind.CHAPTER, 7),
                    (TranslationEventKind.ITEM_COMPLETE, TranslationItemKind.CHAPTER, 7),
                    (TranslationEventKind.ITEM_START, TranslationItemKind.CHAPTER, "head"),
                    (TranslationEventKind.ITEM_COMPLETE, TranslationItemKind.CHAPTER, "head"),
                ],
            )
            self.assertEqual(events[-1].kind, TranslationEventKind.COMPLETE)
            self.assertEqual(events[-1].completed_characters, len("headchapter"))

            progress = [event for event in events if event.kind == TranslationEventKind.PROGRESS]
            self.assertEqual(
                [(event.item_id, event.item_completed_characters, event.item_total_characters)
                 for event in progress],
                [(7, len("chapter"), len("chapter")), ("head", len("head"), len("head"))],
            )


class TestCrossChapterTranslation(unittest.IsolatedAsyncioTestCase):
    async def test_xml_chapters_can_translate_at_the_same_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (10, 10)})
            chapters = [
                Chapter(chapter_id, 1, [TextFlowItem(
                    "body", 0, [SourceTextFragment(
                        1, 1, (1, 1, 5, 5), [f"chapter {chapter_id}"],
                    )],
                )])
                for chapter_id in (1, 2)
            ]
            for chapter in chapters:
                (source_root / "chapters" / f"chapter_{chapter.id}.xml").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    + tostring(encode(chapter), encoding="unicode")
                )

            class GatedTranslator:
                target_language = "en"

                def __init__(self):
                    self.started = 0
                    self.both_started = asyncio.Event()
                    self.release = asyncio.Event()

                async def translate_element(self, task, **_kwargs):
                    self.started += 1
                    if self.started == 2:
                        self.both_started.set()
                    await self.release.wait()
                    return task.element, task.payload

            translator = GatedTranslator()
            transform = ChapterExtractionTransformer(
                ChapterXMLTransformer(translator)
            )
            pending = asyncio.create_task(transform._transform_to_workspace_async(
                source, root / "target",
            ))
            await asyncio.wait_for(translator.both_started.wait(), timeout=1)
            translator.release.set()
            result = await pending
            self.assertTrue(result._validate())

    async def test_chapter_failure_is_direct_and_cancels_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (10, 10)})
            for chapter_id in (1, 2):
                chapter = Chapter(chapter_id, 1, [TextFlowItem(
                    "body", 0, [SourceTextFragment(
                        1, 1, (1, 1, 5, 5), [f"chapter {chapter_id}"],
                    )],
                )])
                (source_root / "chapters" / f"chapter_{chapter_id}.xml").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    + tostring(encode(chapter), encoding="unicode")
                )

            class FailingTranslator:
                target_language = "en"

                def __init__(self):
                    self.started = 0
                    self.both_started = asyncio.Event()
                    self.sibling_cancelled = asyncio.Event()
                    self.never = asyncio.Event()

                async def translate_element(self, task, **_kwargs):
                    self.started += 1
                    position = self.started
                    if self.started == 2:
                        self.both_started.set()
                    await self.both_started.wait()
                    if position == 1:
                        raise NonContinuableError("quota")
                    try:
                        await self.never.wait()
                    except asyncio.CancelledError:
                        self.sibling_cancelled.set()
                        raise
                    return task.element, task.payload

            translator = FailingTranslator()
            transform = ChapterExtractionTransformer(
                ChapterXMLTransformer(translator)
            )
            with self.assertRaises(NonContinuableError):
                await transform._transform_to_workspace_async(
                    source, root / "target",
                )
            self.assertTrue(translator.sibling_cancelled.is_set())
