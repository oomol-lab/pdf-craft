# pylint: disable=protected-access

import asyncio
import json
from pathlib import Path
from shutil import copytree
import tempfile
import unittest
from xml.etree import ElementTree
from zipfile import ZipFile

from epub_generator import BookMeta

from pdf_craft import AsyncPDFCraft, PDFCraft, RenderMode
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceAsset, SourceTextFragment, TextFlowItem, decode, encode,
    search_references_in_chapter,
)
from pdf_craft.extractor.toc.types import Toc, TocInfo, encode as encode_toc
from tests.extraction_helpers import make_extraction


def _source(root: Path):
    extraction = make_extraction(
        root, with_toc=True, language="zh",
        book_meta=BookMeta(title="原书名", description="原描述"),
    )
    asset_hash = "a" * 64
    (root / "assets" / f"{asset_hash}.png").write_bytes(b"fixture")
    reference = Reference(1, 1, "1", [
        TextFlowItem("body", 0, [
            SourceTextFragment(1, 3, (1, 82, 90, 95), ["原注释"]),
        ]),
    ])
    save_xml(encode(Chapter(1, 0, [
        TextFlowItem("heading", 1, [
            SourceTextFragment(1, 1, (1, 1, 90, 10), ["原标题"]),
        ]),
        TextFlowItem("body", 0, [
            SourceTextFragment(1, 2, (1, 12, 90, 30), ["原正文", reference]),
            SourceAsset(
                1, "image", (1, 32, 90, 80), ["图片标题"],
                ["图片内容"], ["图片说明"], asset_hash,
            ),
        ]),
    ])), root / "chapters/chapter_1.xml")
    save_xml(encode_toc(TocInfo([Toc(1, 1, 0, 0, [])], [])), root / "toc.xml")
    return extraction._validate(require_toc=True)


def _add_translation(root: Path, translation_id: str, prefix: str) -> None:
    translations = root / "translations"
    if translations.exists():
        index = json.loads((translations / "index.json").read_text(encoding="utf-8"))
    else:
        translations.mkdir()
        index = {"translations": []}
    layer = translations / translation_id
    copytree(root / "chapters", layer / "chapters")
    chapter_path = layer / "chapters/chapter_1.xml"
    chapter = decode(ElementTree.parse(chapter_path).getroot())
    for item in chapter.flow_items:
        if isinstance(item, TextFlowItem):
            for child in item.children:
                if isinstance(child, SourceTextFragment):
                    child.content = [f"{prefix}:{child.content[0]}", *child.content[1:]]
    for reference in search_references_in_chapter(chapter):
        for item in reference.flow_items:
            if isinstance(item, TextFlowItem):
                for child in item.children:
                    if isinstance(child, SourceTextFragment):
                        child.content = [f"{prefix}:{child.content[0]}", *child.content[1:]]
    save_xml(encode(chapter), chapter_path)
    (layer / "metadata.json").write_text(json.dumps({
        "title": f"{prefix}:Title",
        "description": f"{prefix}:Description",
        "language": "en",
    }), encoding="utf-8")
    (layer / "coverage.xml").write_text(
        "<translation><narrative>"
        "<paragraph chapter_id='1' page_index='1' order='1' state='translated'/>"
        "<paragraph chapter_id='1' page_index='1' order='2' state='translated'/>"
        "</narrative></translation>",
        encoding="utf-8",
    )
    index["translations"].append({
        "id": translation_id,
        "target_language": "en",
        "created_at": "2026-01-01T00:00:00+00:00",
    })
    (translations / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _epub_text(path: Path) -> str:
    with ZipFile(path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith((".xhtml", ".html", ".opf", ".ncx"))
        )


class TestPCEXTranslationRendering(unittest.TestCase):
    def test_source_needs_no_translation_and_translation_modes_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            output = root / "source.md"

            PDFCraft().render_markdown(extraction, output)
            self.assertIn("原正文", output.read_text(encoding="utf-8"))
            for mode in (RenderMode.REPLACE, RenderMode.BILINGUAL):
                with self.subTest(mode=mode), self.assertRaisesRegex(
                    ValueError, "has no translations",
                ):
                    PDFCraft().render_markdown(extraction, root / f"{mode.value}.md", mode=mode)

    def test_default_and_explicit_selection_are_stable_for_same_language_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            _add_translation(root / "source", "english-a", "ONE")
            _add_translation(root / "source", "english-b", "TWO")
            extraction._validate()

            self.assertEqual(PDFCraft().resolve_translation(extraction).id, "english-a")
            self.assertEqual(
                asyncio.run(AsyncPDFCraft().resolve_translation(extraction, "english-b")).id,
                "english-b",
            )
            PDFCraft().render_markdown(
                extraction, root / "default.md", mode=RenderMode.REPLACE,
            )
            PDFCraft().render_markdown(
                extraction, root / "explicit.md", mode=RenderMode.REPLACE,
                translation_id="english-b",
            )
            self.assertIn("ONE:原正文", (root / "default.md").read_text(encoding="utf-8"))
            self.assertIn("TWO:原正文", (root / "explicit.md").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "no translation with id: missing-id"):
                PDFCraft().resolve_translation(extraction, "missing-id")

    def test_markdown_bilingual_merges_heading_and_body_but_not_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            source_xml = (root / "source/chapters/chapter_1.xml").read_bytes()
            _add_translation(root / "source", "english-a", "EN")
            extraction._validate()

            output = root / "bilingual.md"
            PDFCraft().render_markdown(extraction, output, mode=RenderMode.BILINGUAL)
            markdown = output.read_text(encoding="utf-8")
            self.assertIn("# 原标题 EN:原标题", markdown)
            self.assertLess(markdown.index("原正文"), markdown.index("EN:原正文"))
            self.assertIn("原注释", markdown)
            self.assertIn("EN:原注释", markdown)
            self.assertEqual(markdown.count("图片标题"), 1)
            self.assertEqual(
                (root / "source/chapters/chapter_1.xml").read_bytes(), source_xml,
            )

    def test_epub_replace_and_bilingual_apply_content_metadata_and_toc(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            _add_translation(root / "source", "english-a", "EN")
            extraction._validate(require_toc=True)

            replaced = root / "replace.epub"
            bilingual = root / "bilingual.epub"
            PDFCraft().render_epub(extraction, replaced, mode=RenderMode.REPLACE)
            PDFCraft().render_epub(extraction, bilingual, mode=RenderMode.BILINGUAL)
            replace_text = _epub_text(replaced)
            bilingual_text = _epub_text(bilingual)
            self.assertIn("EN:原正文", replace_text)
            self.assertNotIn(">原正文<", replace_text)
            self.assertIn("EN:Title", replace_text)
            self.assertIn("原标题 EN:原标题", bilingual_text)
            self.assertIn("原正文", bilingual_text)
            self.assertIn("EN:原正文", bilingual_text)
            self.assertIn("原书名 EN:Title", bilingual_text)
            self.assertEqual(bilingual_text.count("图片标题"), 1)


if __name__ == "__main__":
    unittest.main()
