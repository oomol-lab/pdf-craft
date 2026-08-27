import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import tostring

from pdf_craft import ChapterPackageTransformer, TranslationEventKind, TranslationItemKind
from pdf_craft.craft import PDFCraft
from pdf_craft.document import DocumentPackage
from pdf_craft.extractor.chapter.chapter import BlockLayout, Chapter, ParagraphLayout, encode


class TestTranslationEvents(unittest.TestCase):
    def test_direct_package_transform_does_not_claim_translation_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DocumentPackage.from_path(root / "source")
            source.chapters_path.mkdir(parents=True)
            source.assets_path.mkdir()
            source.write_metadata(page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [ParagraphLayout(
                "text", 0, [BlockLayout(1, 1, (1, 1, 5, 5), ["source"])]
            )])
            (source.chapters_path / "chapter_head.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )

            class Identity:
                def transform(self, chapter):
                    return chapter

            events = []
            ChapterPackageTransformer(Identity()).transform(
                source, root / "target", on_translation_event=events.append
            )
            self.assertEqual(events, [])

    def test_package_translation_reports_format_neutral_chapter_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DocumentPackage.from_path(root / "source")
            source.chapters_path.mkdir(parents=True)
            source.assets_path.mkdir()
            source.write_metadata(page_pixel_sizes={1: (10, 10)})

            chapters = [
                Chapter(None, -1, [ParagraphLayout(
                    "text", 0, [BlockLayout(1, 1, (1, 1, 5, 5), ["head"])]
                )]),
                Chapter(7, 1, [ParagraphLayout(
                    "text", 0, [BlockLayout(1, 1, (1, 1, 5, 5), ["chapter"])]
                )]),
                Chapter(8, 1, []),
            ]
            for name, chapter in zip(("chapter_head.xml", "chapter_7.xml", "chapter_8.xml"), chapters):
                (source.chapters_path / name).write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    + tostring(encode(chapter), encoding="unicode")
                )

            class Identity:
                def transform(self, chapter):
                    return chapter

            events = []
            PDFCraft().translate_package(
                source, root / "target", Identity(), on_translation_event=events.append
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
