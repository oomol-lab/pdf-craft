"""Behavioral tests for the internal furniture translation pass."""

# pylint: disable=protected-access

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree

from pdf_craft.common import save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, Chapter, TextFlowItem, encode
from pdf_craft.extractor.toc.types import Toc, TocInfo, encode as encode_toc
from pdf_craft.transformer.furniture import FurniturePosition, FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.package import FurnitureExtractionTransformer
from pdf_craft.transformer.furniture_translation import _reconcile_toc_section
from tests.extraction_helpers import make_extraction


class _FurnitureTranslator:
    def __init__(self) -> None:
        self.positions: list[FurniturePosition] = []
        self.pages: list[tuple[int, list[FurnitureSection]]] = []

    def transform_position(self, position: FurniturePosition) -> str | None:
        self.positions.append(position)
        if position.content == "keep position":
            return None
        return f"T:{position.content}"

    def transform_sections(
        self,
        page_index: int,
        sections: Sequence[FurnitureSection],
    ) -> list[str | None]:
        self.pages.append((page_index, list(sections)))
        if page_index == 2:
            raise RuntimeError("page translation failed")
        return [f"T:{section.content}" for section in sections]


class _XMLTaskTranslator:
    def __init__(self) -> None:
        self.tags: list[str] = []

    def translate_element(self, task, **_kwargs):
        self.tags.append(task.element.tag)
        for element in task.element.iter():
            if element.text:
                element.text = f"X:{element.text}"
        return task.element, task.payload


class FurnitureTranslationTests(unittest.TestCase):
    def test_internal_pass_reconciles_toc_and_translates_by_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _translated_narrative_extraction(root / "source")
            translator = _FurnitureTranslator()

            translated = FurnitureExtractionTransformer(translator).transform(
                source, root / "furniture-translated.pcex"
            )

            self.assertEqual(
                [(position.pattern_id, position.position_id, position.content) for position in translator.positions],
                [(1, 1, "Book title"), (1, 2, "keep position")],
            )
            self.assertEqual(
                [(page, [section.content for section in sections]) for page, sections in translator.pages],
                [(1, ["Page one fragment"]), (2, ["Page two fragment"])],
            )
            with translated._materialize() as paths:
                furniture = ElementTree.parse(paths.furnitures).getroot()
                positions = furniture.findall("patterns/pattern/position")
                self.assertEqual(positions[0].text, "第一章")
                self.assertEqual(positions[1].text, "T:Book title")
                self.assertEqual(positions[2].text, "keep position")
                sections = furniture.findall("pages/page/section")
                self.assertEqual(sections[0].text, "1. 第一章 .... 7")
                self.assertEqual(sections[1].text, "T:Page one fragment")
                self.assertIsNone(sections[2].text)
                self.assertEqual(sections[3].text, "Page two fragment")

                coverage = ElementTree.parse(paths.translation).getroot()
                narrative = coverage.find("narrative/paragraph")
                self.assertIsNotNone(narrative)
                assert narrative is not None
                self.assertEqual(narrative.get("state"), "translated")
                states = {
                    (entry.tag, tuple(sorted(entry.attrib.items()))): entry.get("state")
                    for entry in coverage.find("furnitures") or []
                }
                self.assertEqual(
                    states[("position", (("pattern_id", "1"), ("position_id", "0"), ("state", "translated")))],
                    "translated",
                )
                self.assertEqual(
                    states[("position", (("pattern_id", "1"), ("position_id", "1"), ("state", "translated")))],
                    "translated",
                )
                self.assertEqual(
                    states[("position", (("pattern_id", "1"), ("position_id", "2"), ("state", "preserved")))],
                    "preserved",
                )
                self.assertEqual(
                    states[("section", (("det", "1,30,90,50"), ("page_index", "1"), ("state", "translated")))],
                    "translated",
                )
                self.assertEqual(
                    states[("section", (("det", "1,60,90,80"), ("page_index", "1"), ("state", "translated")))],
                    "translated",
                )
                self.assertEqual(
                    states[("section", (("det", "1,60,90,80"), ("page_index", "2"), ("state", "preserved")))],
                    "preserved",
                )
                self.assertFalse(any(entry.get("det") == "1,85,90,95" for entry in coverage.iter("section")))

            PDFCraftExtraction.open(root / "furniture-translated.pcex").validate()

    def test_internal_pass_without_furniture_is_a_valid_no_op(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source")
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            translator = _FurnitureTranslator()

            translated = FurnitureExtractionTransformer(translator).transform(
                source, root / "target.pcex"
            )

            with translated._materialize() as paths:
                self.assertFalse(paths.furnitures.exists())
                self.assertFalse(paths.translation.exists())
            self.assertEqual(translator.positions, [])
            self.assertEqual(translator.pages, [])

    def test_internal_pass_preserves_variable_folio_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source")
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='1' kind='universal'>"
                "<position id='0' folio_style='D' folio_offset='0'/>"
                "</pattern></patterns><pages><page index='1'>"
                "<section det='1,1,90,15'><association kind='universal' pattern_id='1' position_id='0'/>"
                "</section></page></pages></furnitures>",
                encoding="utf-8",
            )
            translator = _FurnitureTranslator()

            translated = FurnitureExtractionTransformer(translator).transform(
                source, root / "target.pcex"
            )

            self.assertEqual(translator.positions, [])
            with translated._materialize() as paths:
                position = ElementTree.parse(paths.furnitures).find("patterns/pattern/position")
                self.assertIsNotNone(position)
                assert position is not None
                self.assertIsNone(position.text)
                coverage = ElementTree.parse(paths.translation).find("furnitures/position")
                self.assertIsNotNone(coverage)
                assert coverage is not None
                self.assertEqual(coverage.get("state"), "preserved")
            translated.validate()

    def test_internal_pass_translates_only_folio_decoration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={2: (100, 100)})
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='1' kind='same_side'>"
                "<position id='0' folio_style='D' folio_offset='0' folio_prefix='Page '/>"
                "</pattern></patterns><pages><page index='2'>"
                "<section det='1,1,90,15'><association kind='same_side' pattern_id='1' position_id='0'/>"
                "</section></page></pages></furnitures>",
                encoding="utf-8",
            )
            translator = _FurnitureTranslator()

            translated = FurnitureExtractionTransformer(translator).transform(
                source, root / "target.pcex"
            )

            self.assertEqual(len(translator.positions), 1)
            self.assertEqual(
                translator.positions[0].content, "Page __PDF_CRAFT_FOLIO__"
            )
            with translated._materialize() as paths:
                position = ElementTree.parse(paths.furnitures).find("patterns/pattern/position")
                self.assertIsNotNone(position)
                assert position is not None
                self.assertEqual(position.get("folio_prefix"), "T:Page ")
                self.assertIsNone(position.text)
                coverage = ElementTree.parse(paths.translation).find("furnitures/position")
                self.assertIsNotNone(coverage)
                assert coverage is not None
                self.assertEqual(coverage.get("state"), "translated")
            translated.validate()

    def test_translation_coverage_rejects_unknown_furniture_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _translated_narrative_extraction(root)
            (root / "translation.xml").write_text(
                "<translation><furnitures>"
                "<position pattern_id='999' position_id='1' state='translated'/>"
                "</furnitures></translation>",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid furniture position"):
                PDFCraftExtraction._from_workspace(root).validate()

    def test_toc_section_preserves_supported_number_prefixes(self):
        cases = [
            ("1. Chapter One .... 7", "第一章", "1. 第一章 .... 7"),
            ("1.2. Chapter One … 8", "第一章", "1.2. 第一章 … 8"),
            ("1.2 Chapter One … 8", "第一章", "1.2 第一章 … 8"),
            ("IV. Chapter One . 9", "第四章", "IV. 第四章 . 9"),
            ("A) Appendix .... 10", "附录", "A) 附录 .... 10"),
            ("1. Chapter One", "1. 第一章", "1. 第一章"),
        ]
        for source, title, expected in cases:
            with self.subTest(source=source):
                section = ElementTree.Element("section", {"toc_id": "7"})
                section.text = source

                self.assertEqual(_reconcile_toc_section(section, {7: title}), "translated")
                self.assertEqual(section.text, expected)

    def test_xml_transformer_keeps_template_and_page_payloads_separate(self):
        translator = _XMLTaskTranslator()
        furniture = FurnitureXMLTransformer(translator)

        self.assertEqual(
            furniture.transform_position(FurniturePosition(1, 2, "universal", "Header")),
            "X:Header",
        )
        self.assertEqual(
            furniture.transform_sections(
                7,
                [
                    FurnitureSection(7, (1, 2, 3, 4), "Left"),
                    FurnitureSection(7, (5, 6, 7, 8), "Right"),
                ],
            ),
            ["X:Left", "X:Right"],
        )
        self.assertEqual(translator.tags, ["furniture-position", "furniture-page"])


def _translated_narrative_extraction(root: Path) -> PDFCraftExtraction:
    make_extraction(root, page_pixel_sizes={1: (100, 100), 2: (100, 100)}, with_toc=True)
    save_xml(encode_toc(TocInfo([Toc(7, 1, 0, 0, [])], [])), root / "toc.xml")
    heading = TextFlowItem(
        "heading", 0, [SourceTextFragment(1, 0, (1, 1, 90, 20), ["第一章"])]
    )
    save_xml(encode(Chapter(7, 0, [heading])), root / "chapters/chapter_7.xml")
    (root / "furnitures.xml").write_text(
        "<furnitures><patterns><pattern id='1' kind='universal'>"
        "<position id='0' toc_id='7'>Chapter One</position>"
        "<position id='1'>Book title</position>"
        "<position id='2'>keep position</position>"
        "</pattern></patterns><pages>"
        "<page index='1'><section det='1,30,90,50' toc_id='7'>1. Chapter One .... 7</section>"
        "<section det='1,60,90,80'>Page one fragment</section>"
        "<section det='1,85,90,95'><association kind='universal' pattern_id='1' position_id='1'/></section>"
        "</page><page index='2'><section det='1,60,90,80'>Page two fragment</section></page>"
        "</pages></furnitures>",
        encoding="utf-8",
    )
    (root / "translation.xml").write_text(
        "<translation><narrative><paragraph chapter_id='7' page_index='1' order='0' state='translated'/>"
        "</narrative></translation>",
        encoding="utf-8",
    )
    return PDFCraftExtraction._from_workspace(root).validate()
