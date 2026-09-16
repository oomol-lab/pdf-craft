from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from zipfile import ZipFile

from PIL import Image as PILImage
from epub_generator import LaTeXRender, TableRender

from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter import (
    Chapter, FlowAssetRef, Reference, SourceAsset, SourceTextFragment, StandaloneAsset,
    TextFlowItem, encode,
)
from pdf_craft.markdown.render.layouts import render_layouts
from pdf_craft.renderer.epub.anchored import float_side
from pdf_craft.renderer.epub.render import render_epub_file


def _fragment(bbox: tuple[int, int, int, int], text: str) -> SourceTextFragment:
    return SourceTextFragment(1, 0, bbox, [text])


def _asset(asset_hash: str, *, ref: str = "image", bbox=(0, 30, 80, 160)) -> SourceAsset:
    return SourceAsset(1, cast(FlowAssetRef, ref), bbox, asset_hash=asset_hash)


def _side_image_flow(asset_hash: str) -> TextFlowItem:
    return TextFlowItem("body", 0, [
        _fragment((100, 0, 300, 200), "Before the illustration."),
        _asset(asset_hash),
        _fragment((100, 40, 300, 180), "After the illustration."),
    ])


def _write_image(assets: Path) -> str:
    temporary = assets / "temporary.png"
    PILImage.new("RGB", (20, 20), "navy").save(temporary)
    asset_hash = sha256(temporary.read_bytes()).hexdigest()
    temporary.rename(assets / f"{asset_hash}.png")
    return asset_hash


def test_only_high_confidence_side_image_gets_a_float_direction():
    flow = _side_image_flow("a" * 64)
    assert float_side(flow, 1) == "start"

    # Tables and geometrically ordinary interleaving remain block content.
    table = TextFlowItem("body", 0, [
        flow.children[0], _asset("b" * 64, ref="table"), flow.children[2],
    ])
    assert float_side(table, 1) is None
    non_overlapping = TextFlowItem("body", 0, [
        _fragment((100, 0, 300, 25), "Before."),
        _asset("c" * 64),
        _fragment((100, 170, 300, 200), "After."),
    ])
    assert float_side(non_overlapping, 1) is None


def test_markdown_keeps_an_anchored_image_as_a_block_in_reading_order():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        assets = root / "assets"
        output_assets = root / "output-assets"
        assets.mkdir()
        output_assets.mkdir()
        asset_hash = _write_image(assets)
        rendered = "".join(render_layouts(
            flow_items=[_side_image_flow(asset_hash)],
            assets_path=assets,
            output_assets_path=output_assets,
            asset_ref_path=Path("output-assets"),
            toc_level=0,
        ))

        assert "Before the illustration." in rendered
        assert f"![](output-assets/{asset_hash}.png)" in rendered
        assert "After the illustration." in rendered
        assert rendered.index("Before") < rendered.index("![](") < rendered.index("After")
        assert "float" not in rendered


def test_markdown_keeps_text_continuous_when_an_anchored_image_cannot_render():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        assets = root / "assets"
        output_assets = root / "output-assets"
        assets.mkdir()
        output_assets.mkdir()
        flow = TextFlowItem("body", 0, [
            _fragment((0, 0, 100, 20), "How-"),
            _asset("a" * 64),
            _fragment((0, 22, 100, 42), "ever"),
        ])

        rendered = "".join(render_layouts(
            flow_items=[flow], assets_path=assets, output_assets_path=output_assets,
            asset_ref_path=Path("output-assets"), toc_level=0,
        ))

        assert rendered == "How-ever"


def test_epub_applies_float_only_to_the_precise_anchored_image_occurrence():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        assets = root / "assets"
        chapters = root / "chapters"
        assets.mkdir()
        chapters.mkdir()
        asset_hash = _write_image(assets)
        flow = _side_image_flow(asset_hash)
        # The same raster also appears standalone.  Hash-based post-processing
        # would incorrectly float both; occurrence markers must not.
        chapter = Chapter(None, -1, [
            flow,
            StandaloneAsset(_asset(asset_hash)),
            TextFlowItem("body", 0, [_fragment((0, 220, 300, 260), "A later paragraph.")]),
        ])
        save_xml(encode(chapter), chapters / "chapter_head.xml")
        epub = root / "book.epub"

        render_epub_file(
            chapters, None, assets, epub, None, None, "en", TableRender.HTML,
            LaTeXRender.MATHML, True, lambda: False,
        )

        with ZipFile(epub) as archive:
            xhtml = archive.read("OEBPS/Text/head.xhtml").decode("utf-8")
            css = archive.read("OEBPS/styles/style.css").decode("utf-8")
        assert "pdf-craft-float-marker" not in xhtml
        assert "pdf-craft-anchored-float pdf-craft-anchored-float-start" in xhtml
        assert xhtml.count("pdf-craft-anchored-float") == 2
        assert "Before the illustration." in xhtml
        assert "After the illustration." in xhtml
        assert xhtml.index("Before") < xhtml.index("pdf-craft-anchored-float") < xhtml.index("After")
        assert "@media screen and (max-width: 35em)" in css


def test_epub_footnote_keeps_an_anchored_asset_in_its_reading_order():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        assets = root / "assets"
        chapters = root / "chapters"
        assets.mkdir()
        chapters.mkdir()
        asset_hash = _write_image(assets)
        footnote = Reference(1, 1, "1", [_side_image_flow(asset_hash)])
        chapter = Chapter(None, -1, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 0, (0, 0, 300, 20), ["Main text", footnote]),
        ])])
        save_xml(encode(chapter), chapters / "chapter_head.xml")
        epub = root / "book.epub"

        render_epub_file(
            chapters, None, assets, epub, None, None, "en", TableRender.HTML,
            LaTeXRender.MATHML, True, lambda: False,
        )

        with ZipFile(epub) as archive:
            xhtml = archive.read("OEBPS/Text/head.xhtml").decode("utf-8")
        footnotes = xhtml[xhtml.index("<aside"):]
        assert "Before the illustration." in footnotes
        assert "After the illustration." in footnotes
        assert "<img " in footnotes
        assert footnotes.index("Before") < footnotes.index("<img ") < footnotes.index("After")
        assert "pdf-craft-anchored-float" not in footnotes
