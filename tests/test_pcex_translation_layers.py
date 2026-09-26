# pylint: disable=protected-access

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree

from pdf_craft import PDFCraft
from pdf_craft.common import save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceAsset, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.extractor.toc.types import Toc, TocInfo, encode as encode_toc
from pdf_craft.transformer import ChapterXMLTransformer
from tests.extraction_helpers import make_extraction


class _Prefix:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.calls = 0

    def transform(self, chapter: Chapter) -> Chapter:
        self.calls += 1
        for item in chapter.flow_items:
            if not isinstance(item, TextFlowItem):
                continue
            for child in item.children:
                if isinstance(child, SourceTextFragment):
                    child.content = [f"{self.prefix}:{child.content[0]}", *child.content[1:]]
        return chapter


class _PrefixXML:
    target_language = "fr"

    def translate_element(self, task, **_kwargs):
        for element in task.element.iter():
            if element.tag != "mark" and element.text and element.text.strip():
                element.text = f"fr:{element.text}"
        return task.element, task.payload


def _source(root: Path) -> PDFCraftExtraction:
    extraction = make_extraction(root, page_pixel_sizes={1: (100, 100)})
    asset_hash = "a" * 64
    (root / "assets" / f"{asset_hash}.png").write_bytes(b"not-decoded-by-validation")
    reference = Reference(
        1, 1, "1", [TextFlowItem("body", 0, [
            SourceTextFragment(1, 2, (1, 60, 50, 70), ["reference"]),
        ])],
    )
    save_xml(encode(Chapter(None, -1, [TextFlowItem("body", 0, [
        SourceTextFragment(1, 1, (1, 1, 50, 10), ["source", reference]),
        SourceAsset(
            1, "image", (1, 11, 50, 50), ["asset title"],
            ["asset text"], ["asset caption"], asset_hash,
        ),
    ])])), root / "chapters/chapter_head.xml")
    return extraction._validate()


class TestPCEXTranslationLayers(unittest.TestCase):
    def test_same_language_layers_are_independent_and_keep_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / "source")
            source_xml = (root / "source/chapters/chapter_head.xml").read_bytes()

            first = PDFCraft().translate_extraction(
                source, root / "first.pcex", _Prefix("one"),
                translation_id="english-a", target_language="en",
            )
            second = PDFCraft().translate_extraction(
                first, root / "second.pcex", _Prefix("two"),
                translation_id="english-b", target_language="en",
            )

            self.assertEqual(
                [(item.id, item.target_language) for item in PDFCraft().list_translations(second)],
                [("english-a", "en"), ("english-b", "en")],
            )
            with second._materialize() as paths:
                self.assertEqual((paths.chapters / "chapter_head.xml").read_bytes(), source_xml)
                for translation_id, prefix in (("english-a", "one"), ("english-b", "two")):
                    layer = paths.translations / translation_id
                    chapter = (layer / "chapters/chapter_head.xml").read_text(encoding="utf-8")
                    self.assertIn(f"{prefix}:source", chapter)
                    self.assertIn("asset title", chapter)
                    self.assertIn("asset text", chapter)
                    self.assertIn("asset caption", chapter)
                    self.assertEqual(
                        json.loads((layer / "metadata.json").read_text(encoding="utf-8")),
                        {"language": "en"},
                    )

    def test_generated_id_is_short_and_discoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translated = PDFCraft().translate_extraction(
                _source(root / "source"), root / "translated.pcex", _Prefix("translated"),
                target_language="fr",
            )
            info = PDFCraft().list_translations(translated)[0]
            self.assertEqual(len(info.id), 8)
            self.assertTrue(all(character in "0123456789abcdef" for character in info.id))
            self.assertEqual(info.target_language, "fr")

    def test_xml_translation_writes_an_isolated_metadata_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / "source")
            manifest = json.loads((root / "source/manifest.json").read_text(encoding="utf-8"))
            manifest["document"]["title"] = "Source title"
            manifest["document"]["description"] = "Source description"
            manifest["document"]["subjects"] = ["history", "science"]
            (root / "source/manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8",
            )
            source._validate()

            translated = PDFCraft().translate_extraction(
                source, root / "translated.pcex", ChapterXMLTransformer(_PrefixXML()),
                translation_id="french-main",
            )
            with translated._materialize() as paths:
                source_document = json.loads(paths.manifest.read_text(encoding="utf-8"))["document"]
                overlay = json.loads(
                    (paths.translations / "french-main/metadata.json").read_text(
                        encoding="utf-8"
                    )
                )
            self.assertEqual(source_document["title"], "Source title")
            self.assertEqual(overlay["title"], "fr:Source title")
            self.assertEqual(overlay["description"], "fr:Source description")
            self.assertEqual(overlay["subjects"], ["fr:history", "fr:science"])
            self.assertEqual(overlay["language"], "fr")

    def test_duplicate_id_is_rejected_before_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = PDFCraft().translate_extraction(
                _source(root / "source"), root / "first.pcex", _Prefix("one"),
                translation_id="same-id", target_language="en",
            )
            transformer = _Prefix("two")
            with self.assertRaisesRegex(ValueError, "already exists"):
                PDFCraft().translate_extraction(
                    first, root / "duplicate.pcex", transformer,
                    translation_id="same-id", target_language="en",
                )
            self.assertEqual(transformer.calls, 0)
            self.assertFalse((root / "duplicate.pcex").exists())

    def test_source_chapter_filename_and_toc_identities_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            head = _source(root / "head")
            head_path = root / "head/chapters/chapter_head.xml"
            head_xml = ElementTree.parse(head_path)
            head_xml.getroot().set("id", "1")
            head_xml.write(head_path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "does not match its chapter id"):
                head._validate()

            numeric = _source(root / "numeric")
            (root / "numeric/chapters/chapter_head.xml").rename(
                root / "numeric/chapters/chapter_1.xml"
            )
            with self.assertRaisesRegex(ValueError, "does not match its chapter id"):
                numeric._validate()

            wrong_numeric = _source(root / "wrong-numeric")
            wrong_path = root / "wrong-numeric/chapters/chapter_head.xml"
            wrong_xml = ElementTree.parse(wrong_path)
            wrong_xml.getroot().set("id", "2")
            wrong_xml.write(
                root / "wrong-numeric/chapters/chapter_1.xml",
                encoding="utf-8",
                xml_declaration=True,
            )
            wrong_path.unlink()
            with self.assertRaisesRegex(ValueError, "does not match its chapter id"):
                wrong_numeric._validate()

            mismatch = make_extraction(root / "toc", with_toc=True)
            save_xml(
                encode_toc(TocInfo([Toc(1, 1, 0, 0, [])], [])),
                root / "toc/toc.xml",
            )
            save_xml(
                encode(Chapter(2, 0, [])),
                root / "toc/chapters/chapter_2.xml",
            )
            with self.assertRaisesRegex(ValueError, "chapter IDs do not match toc.xml"):
                mismatch._validate()

    def test_layer_structure_identity_and_geometry_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translated = PDFCraft().translate_extraction(
                _source(root / "source"), root / "translated.pcex", _Prefix("translated"),
                translation_id="layer-one", target_language="en",
            )
            with translated._materialize() as paths:
                chapter_path = paths.translations / "layer-one/chapters/chapter_head.xml"
                chapter = ElementTree.parse(chapter_path)
                fragment = chapter.find("flow/text/fragment")
                assert fragment is not None
                fragment.set("source_order", "99")
                chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
                with self.assertRaisesRegex(ValueError, "preserve source structure and geometry"):
                    PDFCraftExtraction._from_workspace(paths.root)._validate()

            mutations = {
                "duplicate-fragment": lambda chapter: chapter.find("flow/text").append(
                    deepcopy(chapter.find("flow/text/fragment"))
                ),
                "duplicate-reference": lambda chapter: chapter.find("references").append(
                    deepcopy(chapter.find("references/ref"))
                ),
                "paragraph-flow": lambda chapter: chapter.find("flow").append(
                    ElementTree.Element("text", {"role": "body"})
                ),
                "fragment-geometry": lambda chapter: chapter.find(
                    "flow/text/fragment"
                ).set("bbox", "2,2,50,10"),
            }
            for name, mutate in mutations.items():
                with self.subTest(name=name), translated._materialize() as paths:
                    chapter_path = paths.translations / "layer-one/chapters/chapter_head.xml"
                    chapter = ElementTree.parse(chapter_path)
                    mutate(chapter)
                    chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
                    with self.assertRaisesRegex(
                        ValueError, "preserve source structure and geometry"
                    ):
                        PDFCraftExtraction._from_workspace(paths.root)._validate()

            with translated._materialize() as paths:
                chapter_path = paths.translations / "layer-one/chapters/chapter_head.xml"
                chapter = ElementTree.parse(chapter_path)
                title = chapter.find("flow/text/asset/title")
                assert title is not None
                title.text = "changed"
                chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
                with self.assertRaisesRegex(ValueError, "preserve source structure and geometry"):
                    PDFCraftExtraction._from_workspace(paths.root)._validate()

    def test_layer_furniture_identity_and_geometry_must_match_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / "source")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='0' kind='universal'>"
                "<position id='0'>Header</position></pattern></patterns>"
                "<pages><page index='1'><section det='1,75,90,90'>Footer</section>"
                "</page></pages></furnitures>",
                encoding="utf-8",
            )
            source._validate()
            translated = PDFCraft().translate_extraction(
                source,
                root / "translated.pcex",
                ChapterXMLTransformer(_PrefixXML()),
                with_furniture=True,
                translation_id="layer-one",
            )

            with translated._materialize() as paths:
                layer = paths.translations / "layer-one"
                furniture = ElementTree.parse(layer / "furnitures.xml")
                position = furniture.find("patterns/pattern/position")
                assert position is not None
                position.set("id", "1")
                furniture.write(layer / "furnitures.xml", encoding="utf-8", xml_declaration=True)
                coverage = ElementTree.parse(layer / "coverage.xml")
                coverage_position = coverage.find("furnitures/position")
                assert coverage_position is not None
                coverage_position.set("position_id", "1")
                coverage.write(layer / "coverage.xml", encoding="utf-8", xml_declaration=True)
                with self.assertRaisesRegex(ValueError, "preserve source structure"):
                    PDFCraftExtraction._from_workspace(paths.root)._validate()

            with translated._materialize() as paths:
                layer = paths.translations / "layer-one"
                furniture = ElementTree.parse(layer / "furnitures.xml")
                section = furniture.find("pages/page/section")
                assert section is not None
                section.set("det", "2,75,90,90")
                furniture.write(layer / "furnitures.xml", encoding="utf-8", xml_declaration=True)
                coverage = ElementTree.parse(layer / "coverage.xml")
                coverage_section = coverage.find("furnitures/section")
                assert coverage_section is not None
                coverage_section.set("det", "2,75,90,90")
                coverage.write(layer / "coverage.xml", encoding="utf-8", xml_declaration=True)
                with self.assertRaisesRegex(ValueError, "preserve source structure"):
                    PDFCraftExtraction._from_workspace(paths.root)._validate()


if __name__ == "__main__":
    unittest.main()
