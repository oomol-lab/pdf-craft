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
    Chapter, DisplayFormula, Reference, SourceAsset, SourceTextFragment, TextFlowItem,
    decode, encode, search_references_in_chapter,
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


def _epub_documents(path: Path) -> tuple[tuple[str, str], ...]:
    with ZipFile(path) as archive:
        return tuple(
            (name, archive.read(name).decode("utf-8"))
            for name in sorted(archive.namelist())
            if name.endswith((".xhtml", ".html"))
        )


def _legacy_archive(root: Path, version: int) -> Path:
    workspace = root / f"v{version}"
    make_extraction(workspace, with_toc=True, language="en")
    (workspace / "assets" / f"{'f' * 64}.png").write_bytes(b"unused")
    chapter_path = workspace / "chapters/chapter_head.xml"
    if version in {1, 2}:
        chapter_path.write_text(
            "<chapter><body><paragraph ref='text'>"
            "<block page_index='1' order='1' det='1,1,90,20'>Legacy source</block>"
            "</paragraph></body></chapter>",
            encoding="utf-8",
        )
    else:
        save_xml(encode(Chapter(None, -1, [
            TextFlowItem("body", 0, [
                SourceTextFragment(1, 1, (1, 1, 90, 20), ["Legacy source"]),
            ]),
        ])), chapter_path)
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = version
    if version == 1:
        document = manifest["document"]
        manifest["document"] = {
            key: ([] if key in {"authors", "editors", "translators"} else document[key])
            for key in (
                "title", "description", "publisher", "isbn", "authors", "editors",
                "translators", "modified", "language",
            )
        }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive_path = root / f"v{version}.pcex"
    with ZipFile(archive_path, "w") as archive:
        for path in workspace.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(workspace).as_posix())
    return archive_path


class TestPCEXTranslationRendering(unittest.TestCase):
    def test_sync_and_async_render_facades_match_for_every_mode_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            _add_translation(root / "source", "english-a", "ONE")
            _add_translation(root / "source", "english-b", "TWO")
            extraction._validate(require_toc=True)

            selections = (
                (RenderMode.SOURCE, None),
                (RenderMode.REPLACE, None),
                (RenderMode.BILINGUAL, "english-b"),
            )
            for mode, translation_id in selections:
                with self.subTest(mode=mode, translation_id=translation_id):
                    sync_markdown = root / f"sync-{mode.value}.md"
                    async_markdown = root / f"async-{mode.value}.md"
                    sync_epub = root / f"sync-{mode.value}.epub"
                    async_epub = root / f"async-{mode.value}.epub"
                    assets_path = Path(f"{mode.value}-assets")
                    PDFCraft().render_markdown(
                        extraction, sync_markdown, assets_path,
                        mode=mode, translation_id=translation_id,
                    )
                    PDFCraft().render_epub(
                        extraction, sync_epub, mode=mode,
                        translation_id=translation_id,
                    )
                    asyncio.run(AsyncPDFCraft().render_markdown(
                        extraction, async_markdown, assets_path,
                        mode=mode, translation_id=translation_id,
                    ))
                    asyncio.run(AsyncPDFCraft().render_epub(
                        extraction, async_epub, mode=mode,
                        translation_id=translation_id,
                    ))
                    self.assertEqual(
                        sync_markdown.read_text(encoding="utf-8"),
                        async_markdown.read_text(encoding="utf-8"),
                    )
                    self.assertEqual(
                        _epub_documents(sync_epub), _epub_documents(async_epub),
                    )

            with self.assertRaisesRegex(ValueError, "translation_id must be"):
                asyncio.run(AsyncPDFCraft().render_markdown(
                    extraction, root / "async-invalid.md", mode=RenderMode.REPLACE,
                    translation_id="bad/id",
                ))
            with self.assertRaisesRegex(ValueError, "no translation with id: missing-id"):
                asyncio.run(AsyncPDFCraft().render_epub(
                    extraction, root / "async-missing.epub", mode=RenderMode.BILINGUAL,
                    translation_id="missing-id",
                ))

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
            with self.assertRaisesRegex(ValueError, "translation_id must be"):
                PDFCraft().resolve_translation(extraction, "bad/id")
            with self.assertRaisesRegex(ValueError, "translation_id must be"):
                PDFCraft().render_markdown(
                    extraction, root / "invalid.md", mode=RenderMode.REPLACE,
                    translation_id="bad/id",
                )

    def test_replace_falls_back_for_preserved_and_missing_narrative_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            _add_translation(root / "source", "english-a", "EN")
            layer = root / "source/translations/english-a"
            chapter_path = layer / "chapters/chapter_1.xml"
            chapter = ElementTree.parse(chapter_path)
            fragments = chapter.findall("flow/text/fragment")
            fragments[0].text = None
            fragments[1].text = None
            chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
            coverage = ElementTree.parse(layer / "coverage.xml")
            narrative = coverage.find("narrative")
            assert narrative is not None
            narrative.remove(narrative[0])
            narrative[0].set("state", "preserved")
            coverage.write(layer / "coverage.xml", encoding="utf-8", xml_declaration=True)
            extraction._validate(require_toc=True)

            markdown_path = root / "replace.md"
            epub_path = root / "replace.epub"
            PDFCraft().render_markdown(extraction, markdown_path, mode=RenderMode.REPLACE)
            PDFCraft().render_epub(extraction, epub_path, mode=RenderMode.REPLACE)
            markdown = markdown_path.read_text(encoding="utf-8")
            epub = _epub_text(epub_path)
            self.assertIn("原标题", markdown)
            self.assertIn("原正文", markdown)
            self.assertIn("原标题", epub)
            self.assertIn("原正文", epub)

    def test_reference_uses_identity_and_target_presence_not_text_equality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            _add_translation(root / "source", "english-a", "EN")
            chapter_path = root / "source/translations/english-a/chapters/chapter_1.xml"
            chapter = ElementTree.parse(chapter_path)
            target = chapter.find("references/ref/flow/text/fragment")
            assert target is not None
            target.text = None
            chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
            extraction._validate()

            preserved_path = root / "preserved.md"
            preserved_epub = root / "preserved.epub"
            PDFCraft().render_markdown(
                extraction, preserved_path, mode=RenderMode.BILINGUAL,
            )
            PDFCraft().render_epub(
                extraction, preserved_epub, mode=RenderMode.BILINGUAL,
            )
            self.assertEqual(
                preserved_path.read_text(encoding="utf-8").count("原注释"), 1,
            )
            self.assertEqual(_epub_text(preserved_epub).count("原注释"), 1)

            chapter = ElementTree.parse(chapter_path)
            target = chapter.find("references/ref/flow/text/fragment")
            assert target is not None
            target.text = "原注释"
            chapter.write(chapter_path, encoding="utf-8", xml_declaration=True)
            extraction._validate()
            equal_path = root / "equal.md"
            equal_epub = root / "equal.epub"
            PDFCraft().render_markdown(
                extraction, equal_path, mode=RenderMode.BILINGUAL,
            )
            PDFCraft().render_epub(
                extraction, equal_epub, mode=RenderMode.BILINGUAL,
            )
            self.assertEqual(equal_path.read_text(encoding="utf-8").count("原注释"), 2)
            self.assertEqual(_epub_text(equal_epub).count("原注释"), 2)

    def test_legacy_v1_v2_v3_source_layers_render_to_markdown_and_epub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in (1, 2, 3):
                with self.subTest(version=version):
                    archive = _legacy_archive(root, version)
                    markdown = root / f"v{version}.md"
                    epub = root / f"v{version}.epub"
                    PDFCraft().render_markdown(archive, markdown)
                    PDFCraft().render_epub(archive, epub)
                    self.assertIn("Legacy source", markdown.read_text(encoding="utf-8"))
                    self.assertIn("Legacy source", _epub_text(epub))

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

    def test_bilingual_heading_appends_the_complete_translation_after_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            chapter_path = root / "source/chapters/chapter_1.xml"
            chapter = decode(ElementTree.parse(chapter_path).getroot())
            heading = chapter.flow_items[0]
            assert isinstance(heading, TextFlowItem)
            heading.children.append(
                SourceTextFragment(1, 4, (1, 10, 90, 12), ["续"]),
            )
            save_xml(encode(chapter), chapter_path)
            _add_translation(root / "source", "english-a", "EN")
            extraction._validate(require_toc=True)

            output = root / "bilingual.md"
            PDFCraft().render_markdown(extraction, output, mode=RenderMode.BILINGUAL)

            heading_line = output.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(heading_line, "## 原标题续 EN:原标题EN:续")

    def test_formula_translation_renders_in_chapters_and_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source(root / "source")
            chapter_path = root / "source/chapters/chapter_1.xml"
            chapter = decode(ElementTree.parse(chapter_path).getroot())
            chapter.flow_items.insert(1, DisplayFormula(SourceAsset(
                1, "formula", (1, 30, 90, 45), ["原公式标题"],
                [r"x^2"], ["原公式说明"],
            )))
            reference = next(search_references_in_chapter(chapter))
            reference.flow_items.append(DisplayFormula(SourceAsset(
                1, "formula", (1, 85, 90, 98), ["原注释公式标题"],
                [r"y^2"], ["原注释公式说明"],
            )))
            save_xml(encode(chapter), chapter_path)
            _add_translation(root / "source", "english-a", "EN")

            translated_path = (
                root / "source/translations/english-a/chapters/chapter_1.xml"
            )
            translated = decode(ElementTree.parse(translated_path).getroot())
            formula = translated.flow_items[1]
            assert isinstance(formula, DisplayFormula)
            formula.asset.title = ["Translated formula title"]
            formula.asset.caption = ["Translated formula caption"]
            translated_reference = next(search_references_in_chapter(translated))
            reference_formula = translated_reference.flow_items[1]
            assert isinstance(reference_formula, DisplayFormula)
            reference_formula.asset.title = ["Translated note formula title"]
            reference_formula.asset.caption = ["Translated note formula caption"]
            save_xml(encode(translated), translated_path)
            extraction._validate(require_toc=True)

            markers = (
                ("原公式标题", "Translated formula title"),
                ("原公式说明", "Translated formula caption"),
                ("原注释公式标题", "Translated note formula title"),
                ("原注释公式说明", "Translated note formula caption"),
            )
            for mode in (RenderMode.REPLACE, RenderMode.BILINGUAL):
                with self.subTest(mode=mode):
                    markdown_path = root / f"{mode.value}.md"
                    epub_path = root / f"{mode.value}.epub"
                    PDFCraft().render_markdown(extraction, markdown_path, mode=mode)
                    PDFCraft().render_epub(extraction, epub_path, mode=mode)
                    outputs = (
                        markdown_path.read_text(encoding="utf-8"),
                        _epub_text(epub_path),
                    )
                    for output in outputs:
                        for source, target in markers:
                            self.assertIn(target, output)
                            if mode == RenderMode.REPLACE:
                                self.assertNotIn(source, output)
                            else:
                                self.assertIn(source, output)
                    markdown = outputs[0]
                    self.assertEqual(markdown.count("x^2"), 1)
                    self.assertEqual(markdown.count("y^2"), 1)

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
