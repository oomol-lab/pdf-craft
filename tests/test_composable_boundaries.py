# pylint: disable=protected-access

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import cast
from xml.etree.ElementTree import tostring
from PIL import Image
from epub_generator import BookMeta

from pdf_craft.error import NoUsableOCRPagesError, OCRError
from pdf_craft.extractor import PDFExtractor
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.pipeline.pdf import PDFPatcher
from pdf_craft.transformer import (
    ChapterExtractionTransformer, ChapterXMLTransformer,
)
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.package import FurnitureExtractionTransformer
from pdf_craft.renderer import EpubRenderer, MarkdownRenderer
from pdf_craft.extractor.chapter.chapter import SourceAsset, SourceTextFragment, Chapter, TextFlowItem, encode
from pdf_craft.common import save_xml
from pdf_craft.ocr_config import DeepSeekOCRLocalConfig
from pdf_craft.pdf.ocr import OCR, OCREvent, OCREventKind
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pdf.types import Page
from pdf_craft.transform import PDFExtractionEngine
from tests.extraction_helpers import make_extraction


class _FakeTransform:
    def extract_package(self, *, analysing_path, **_kwargs):
        make_extraction(
            analysing_path / "extraction", page_pixel_sizes={1: (100, 100)},
            with_toc=True,
        )
        return None, None, None, None, "metering"


class _NoAssetTransform:
    def extract_package(self, *, analysing_path, **_kwargs):
        make_extraction(analysing_path / "extraction", page_pixel_sizes={1: (100, 100)})
        return None, None, None, None, "metering"


class _CapturePatcher:
    def __init__(self):
        self.replacements = []

    def patch(self, _source, _target, replacements):
        self.replacements = list(replacements)


class _AllPagesFailOCR:
    last_page_pixel_sizes: dict[int, tuple[int, int]] = {}

    def recognize(self, **_kwargs):
        for page_index in (1, 2):
            yield OCREvent(
                OCREventKind.FAILED,
                page_index,
                2,
                error=OCRError("vendor rejected the request", page_index, 0),
            )


class _FakeDocument:
    pages_count = 1

    def metadata(self):
        raise AssertionError("metadata is not used by this OCR test")

    def page_size(self, page_index):
        del page_index
        return (1.0, 1.0)

    def render_page(self, *, page_index, dpi):
        del page_index, dpi
        self.render_count += 1
        return Image.new("RGB", (100, 100))

    def __init__(self):
        self.render_count = 0

    def close(self):
        pass


class _FakeHandler:
    def __init__(self):
        self.document = _FakeDocument()

    def open(self, pdf_path):
        del pdf_path
        return self.document


class _DeterministicXMLTranslator:
    def __init__(self):
        self.calls = 0

    def translate_element(self, task, **_kwargs):
        self.calls += 1
        for node in task.element.iter():
            if node.text:
                node.text = "T:" + node.text
            if node.tail:
                node.tail = "T:" + node.tail
        return task.element, task.payload


class TestComposableBoundaries(unittest.TestCase):
    def test_pdf_patch_uses_only_translated_coverage_and_keeps_preserved_text_as_obstacle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [
                TextFlowItem("body", 0, [SourceTextFragment(1, 1, (1, 1, 90, 30), ["translated"])]),
                TextFlowItem("body", 0, [SourceTextFragment(1, 2, (1, 40, 90, 70), ["preserved"])]),
            ])), root / "source/chapters/chapter_head.xml")
            (root / "source/translation.xml").write_text(
                "<translation><narrative><paragraph chapter_id='head' page_index='1' order='1' state='translated'/>"
                "</narrative></translation>", encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual([replacement.text for replacement in capture.replacements], ["translated"])
            self.assertEqual(
                [region.bbox for region in capture.replacements[0].obstacle_regions], [(1, 40, 90, 70)],
            )

    def test_pdf_patch_keeps_one_covered_text_flow_continuous_across_asset_anchor(self):
        """Coverage patching shares the same continuous-flow rule as translate()."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [
                TextFlowItem("body", 0, [
                    SourceTextFragment(1, 1, (1, 1, 90, 20), ["translated before "]),
                    SourceAsset(1, "image", (1, 22, 90, 70)),
                    SourceTextFragment(1, 2, (1, 72, 90, 95), ["translated after"]),
                ]),
            ])), root / "source/chapters/chapter_head.xml")
            (root / "source/translation.xml").write_text(
                "<translation><narrative><paragraph chapter_id='head' page_index='1' order='1' state='translated'/>"
                "</narrative></translation>", encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual(len(capture.replacements), 1)
            replacement = capture.replacements[0]
            self.assertEqual(replacement.text, "translated before translated after")
            self.assertEqual([region.bbox for region in replacement.regions], [
                (1, 1, 90, 20), (1, 72, 90, 95),
            ])
            self.assertEqual(replacement.obstacle_regions, ())

    def test_pdf_patch_places_translated_furniture_at_associated_and_standalone_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='7' kind='universal'><position id='3'>Header</position>"
                "</pattern></patterns><pages><page index='1'>"
                "<section det='1,1,90,15'><association kind='universal' pattern_id='7' position_id='3'/></section>"
                "<section det='1,80,90,95'>Footer</section>"
                "</page></pages></furnitures>", encoding="utf-8",
            )
            (root / "source/translation.xml").write_text(
                "<translation><furnitures><position pattern_id='7' position_id='3' state='translated'/>"
                "<section page_index='1' det='1,80,90,95' state='translated'/></furnitures></translation>",
                encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual(
                [(replacement.text, replacement.bbox) for replacement in capture.replacements], [
                    ("Header", (1, 1, 90, 15)), ("Footer", (1, 80, 90, 95)),
                ],
            )

    def test_furniture_translation_and_pdf_patch_rebuild_each_same_side_folio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(
                root / "source", page_pixel_sizes={2: (100, 100), 4: (100, 100), 6: (100, 100)}
            )
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='7' kind='same_side'>"
                "<position id='3' folio_style='D' folio_offset='0' folio_prefix='Page '/>"
                "</pattern></patterns><pages>"
                "<page index='2'><section det='1,1,90,15'><association kind='same_side' pattern_id='7' position_id='3'/></section></page>"
                "<page index='4'><section det='1,1,90,15'><association kind='same_side' pattern_id='7' position_id='3'/></section></page>"
                "<page index='6'><section det='1,1,90,15'><association kind='same_side' pattern_id='7' position_id='3'/></section></page>"
                "</pages></furnitures>",
                encoding="utf-8",
            )
            translated = asyncio.run(FurnitureExtractionTransformer(
                FurnitureXMLTransformer(_DeterministicXMLTranslator())
            ).transform(extraction, root / "translated.pcex"))
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), translated,
            )

            self.assertEqual(
                [(replacement.page_index, replacement.text) for replacement in capture.replacements],
                [(2, "T:Page 2"), (4, "T:Page 4"), (6, "T:Page 6")],
            )

    def test_pdf_patch_orders_narrative_and_furniture_by_source_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100), 2: (100, 100)})
            save_xml(encode(Chapter(None, -1, [
                TextFlowItem("body", 0, [SourceTextFragment(2, 1, (1, 1, 90, 30), ["Narrative"])]),
            ])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='7' kind='universal'><position id='3'>Header</position>"
                "</pattern></patterns><pages><page index='1'>"
                "<section det='1,1,90,15'><association kind='universal' pattern_id='7' position_id='3'/></section>"
                "</page></pages></furnitures>", encoding="utf-8",
            )
            (root / "source/translation.xml").write_text(
                "<translation><narrative><paragraph chapter_id='head' page_index='2' order='1' state='translated'/>"
                "</narrative><furnitures><position pattern_id='7' position_id='3' state='translated'/>"
                "</furnitures></translation>", encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual(
                [(replacement.page_index, replacement.layout_ref) for replacement in capture.replacements],
                [(1, "furniture"), (2, "body")],
            )

    def test_pdf_patch_resolves_every_furniture_association_before_preserving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns>"
                "<pattern id='7' kind='universal'><position id='3'>Old header</position></pattern>"
                "<pattern id='8' kind='same_side'><position id='4'>Translated header</position></pattern>"
                "</patterns><pages><page index='1'>"
                "<section det='1,1,90,15'>"
                "<association kind='universal' pattern_id='7' position_id='3'/>"
                "<association kind='same_side' pattern_id='8' position_id='4'/>"
                "</section></page></pages></furnitures>",
                encoding="utf-8",
            )
            (root / "source/translation.xml").write_text(
                "<translation><furnitures>"
                "<position pattern_id='7' position_id='3' state='preserved'/>"
                "<position pattern_id='8' position_id='4' state='translated'/>"
                "</furnitures></translation>",
                encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual(
                [(replacement.text, replacement.bbox) for replacement in capture.replacements],
                [("Translated header", (1, 1, 90, 15))],
            )

    def test_pdf_patch_preserves_conflicting_translated_furniture_associations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [])), root / "source/chapters/chapter_head.xml")
            (root / "source/furnitures.xml").write_text(
                "<furnitures><patterns>"
                "<pattern id='7' kind='universal'><position id='3'>First</position></pattern>"
                "<pattern id='8' kind='same_side'><position id='4'>Second</position></pattern>"
                "</patterns><pages><page index='1'><section det='1,1,90,15'>"
                "<association kind='universal' pattern_id='7' position_id='3'/>"
                "<association kind='same_side' pattern_id='8' position_id='4'/>"
                "</section></page></pages></furnitures>",
                encoding="utf-8",
            )
            (root / "source/translation.xml").write_text(
                "<translation><furnitures>"
                "<position pattern_id='7' position_id='3' state='translated'/>"
                "<position pattern_id='8' position_id='4' state='translated'/>"
                "</furnitures></translation>",
                encoding="utf-8",
            )
            capture = _CapturePatcher()

            PDFTranslationPipeline(patcher=cast(PDFPatcher, capture)).patch(
                Path("input.pdf"), Path("output.pdf"), extraction,
            )

            self.assertEqual(capture.replacements, [])

    def test_extraction_rejects_all_pages_ignored_after_ocr_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = object.__new__(PDFExtractionEngine)
            setattr(engine, "_ocr", cast(OCR, _AllPagesFailOCR()))

            with patch("pdf_craft.transform.analyse_toc") as analyse_toc, self.assertRaisesRegex(
                NoUsableOCRPagesError, "no usable pages"
            ) as raised:
                engine.extract_package(
                    pdf_path=root / "input.pdf",
                    analysing_path=root / "package",
                    ocr_size="gundam",
                    dpi=None,
                    max_page_image_file_size=None,
                    includes_cover=False,
                    includes_footnotes=False,
                    ignore_pdf_errors=False,
                    ignore_ocr_errors=True,
                    generate_plot=False,
                    toc_llm=None,
                    toc_assumed=False,
                    aborted=lambda: False,
                    max_tokens=None,
                    max_output_tokens=None,
                    on_ocr_event=lambda _: None,
                )

            self.assertEqual(raised.exception.failed_page_indexes, (1, 2))
            analyse_toc.assert_not_called()

    def test_extraction_translation_skips_empty_chapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            empty = Chapter(None, 0, [])
            text = Chapter(None, 0, [TextFlowItem(
                "body", 0, [SourceTextFragment(1, 1, (1, 1, 50, 50), ["text"])]
            )])
            (source_root / "chapters/chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(empty), encoding="unicode")
            )
            (source_root / "chapters/chapter_2.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(text), encoding="unicode")
            )

            translator = _DeterministicXMLTranslator()
            target = asyncio.run(ChapterExtractionTransformer(
                ChapterXMLTransformer(translator)
            ).transform(source, root / "target.pcex"))

            self.assertEqual(translator.calls, 1)
            with target._materialize() as paths:
                self.assertEqual(
                    (paths.chapters / "chapter_1.xml").read_text(),
                    (source_root / "chapters/chapter_1.xml").read_text(),
                )
                self.assertIn("T:text", (paths.chapters / "chapter_2.xml").read_text())

    def test_extractor_creates_empty_assets_directory_for_asset_free_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, _ = PDFExtractor(_NoAssetTransform())._extract_with_metering_sync(
                root / "input.pdf", root / "book.pcex"
            )
            with extraction._materialize() as paths:
                self.assertTrue(paths.assets.is_dir())

    def test_extractor_produces_extraction_consumed_without_analysis_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction, metering = PDFExtractor(_FakeTransform())._extract_with_metering_sync(
                root / "input.pdf", root / "book.pcex", analysing_path=root / "analysis"
            )
            self.assertEqual(metering, "metering")
            self.assertFalse((root / "analysis" / "extraction" / "ocr").exists())
            self.assertEqual(extraction._page_pixel_sizes(), {1: (100, 100)})
            with patch("pdf_craft.renderer.markdown.renderer.render_markdown_file") as markdown:
                MarkdownRenderer()._render_blocking(extraction, root / "book.md")
            self.assertEqual(markdown.call_args.args[0].name, "chapters")
            with patch("pdf_craft.renderer.epub.renderer.render_epub_file") as epub:
                EpubRenderer()._render_blocking(extraction, root / "book.epub")
            self.assertEqual(epub.call_args.args[0].name, "chapters")

    def test_explicit_epub_book_meta_overrides_only_its_nonempty_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(
                root,
                page_pixel_sizes={1: (100, 100)},
                with_toc=True,
                book_meta=BookMeta(
                    title="OCR title",
                    description="OCR description",
                    publisher="OCR press",
                    isbn="978-1-4028-9462-6",
                    authors=["OCR author"],
                    editors=["OCR editor"],
                    translators=["OCR translator"],
                ),
                language="en",
            )
            with patch("pdf_craft.renderer.epub.renderer.render_epub_file") as epub:
                EpubRenderer()._render_blocking(
                    extraction,
                    root / "book.epub",
                    book_meta=BookMeta(title="Manual title", description=""),
                )

            metadata = epub.call_args.args[5]
            self.assertEqual(metadata.title, "Manual title")
            self.assertEqual(metadata.description, "OCR description")
            self.assertEqual(metadata.publisher, "OCR press")
            self.assertEqual(metadata.isbn, "978-1-4028-9462-6")
            self.assertEqual(metadata.authors, ["OCR author"])
            self.assertEqual(metadata.editors, ["OCR editor"])
            self.assertEqual(metadata.translators, ["OCR translator"])

    def test_pdf_pipeline_never_recovers_missing_geometry_from_source_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root, page_pixel_sizes={1: (100, 100)})
            chapter = Chapter(None, -1, [TextFlowItem(
                "body", 0, [SourceTextFragment(2, 1, (1, 1, 50, 50), ["text"])]
            )])
            handler = _FakeHandler()
            patcher = _CapturePatcher()
            with patch(
                "pdf_craft.pipeline.pdf.pipeline.create_chapters_reader",
                return_value=lambda: iter([chapter]),
            ), self.assertRaisesRegex(ValueError, "pages.xml is missing page 2"):
                PDFTranslationPipeline(
                    pdf_handler=cast(PDFHandler, handler),
                    patcher=cast(PDFPatcher, patcher),
                ).patch(root / "input.pdf", root / "out.pdf", extraction)
            self.assertEqual(handler.document.render_count, 0)
            self.assertEqual(patcher.replacements, [])

    def test_pages_xml_is_used_for_direct_workspace_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root, page_pixel_sizes={1: (30, 30)})
            self.assertEqual(extraction._page_pixel_sizes(), {1: (30, 30)})

    def test_epub_renderer_rejects_unsupported_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root, with_toc=True)
            with self.assertRaises(ValueError):
                EpubRenderer()._render_blocking(extraction, root / "book.epub", lan="fr")  # type: ignore[arg-type]

    def test_ocr_geometry_cache_survives_interrupted_resume_without_rerendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handler = _FakeHandler()
            first = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, handler))
            page = Page(1, None, [], [], 0, 0)
            with patch("pdf_craft.pdf.ocr.PageExtractorNode.image2page", return_value=page):
                events = first.recognize(root / "input.pdf", root / "assets", root / "ocr")
                while next(events).kind.name != "COMPLETE":
                    pass
                events.close()
            self.assertEqual(first.last_page_pixel_sizes, {1: (100, 100)})
            self.assertEqual(handler.document.render_count, 1)

            resumed = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, handler))
            list(resumed.recognize(root / "input.pdf", root / "assets", root / "ocr"))
            self.assertEqual(resumed.last_page_pixel_sizes, {1: (100, 100)})
            self.assertEqual(handler.document.render_count, 1)

    def test_ignored_ocr_failure_is_retried_instead_of_cached_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handler = _FakeHandler()
            first = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, handler))
            error = OCRError("vendor rejected the request", 1, 0)
            with patch(
                "pdf_craft.pdf.ocr.PageExtractorNode.image2page", side_effect=error
            ):
                events = list(first.recognize(
                    root / "input.pdf", root / "assets", root / "ocr", ignore_ocr_errors=True
                ))
            self.assertEqual(events[-1].kind, OCREventKind.FAILED)
            self.assertTrue((root / "ocr" / "page_1.failed").exists())
            self.assertFalse((root / "ocr" / "done").exists())

            page = Page(1, None, [], [], 0, 0)
            resumed = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, handler))
            with patch("pdf_craft.pdf.ocr.PageExtractorNode.image2page", return_value=page):
                events = list(resumed.recognize(
                    root / "input.pdf", root / "assets", root / "ocr", ignore_ocr_errors=True
                ))
            self.assertEqual(events[-1].kind, OCREventKind.COMPLETE)
            self.assertFalse((root / "ocr" / "page_1.failed").exists())
            self.assertTrue((root / "ocr" / "done").exists())

    def test_extraction_rejects_malformed_page_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root)
            (root / "pages.xml").write_text(
                '<pages index_base="1" coordinate_space="ocr_pixels" render_dpi="300">'
                '<page index="1" width="1" /></pages>'
            )
            with self.assertRaisesRegex(ValueError, "pages.xml"):
                extraction._validate()
            (root / "pages.xml").write_text(
                '<pages index_base="1" coordinate_space="ocr_pixels" render_dpi="300">'
                '<page index="1" width="1.5" height="2" /></pages>'
            )
            with self.assertRaisesRegex(ValueError, "pages.xml"):
                extraction._validate()

    def test_ocr_rejects_malformed_geometry_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ocr_path = root / "ocr"
            ocr_path.mkdir()
            (ocr_path / "page_pixel_sizes.json").write_text('{"1": [1.5, 100]}')
            ocr = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, _FakeHandler()))
            with self.assertRaisesRegex(ValueError, "geometry cache"):
                list(ocr.recognize(root / "input.pdf", root / "assets", ocr_path))
