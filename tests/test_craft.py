# pylint: disable=protected-access

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from xml.etree.ElementTree import fromstring

from epub_generator import BookMeta

from pdf_craft import (
    AsyncPDFCraft, ExtractionOptions, FootnoteOptions, FootnoteRefinement,
    JEV, LLM, PDFCraft, PDFOptions,
)
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor import PDFExtractor
from pdf_craft.extractor.chapter.chapter import SourceTextFragment, Chapter, TextFlowItem, encode
from pdf_craft.common import save_xml
from pdf_craft.transformer import ChapterExtractionTransformer, ChapterXMLTransformer, SubmitKind
from pdf_craft.transformer.package import FurnitureExtractionTransformer
from tests.extraction_helpers import make_extraction


class _Engine:
    def __init__(self):
        self.kwargs = None
        self.analysing_path = None

    def extract_package(self, *, analysing_path, **kwargs):
        self.kwargs = kwargs
        self.analysing_path = analysing_path
        make_extraction(
            analysing_path / "extraction",
            page_pixel_sizes={1: (10, 10)},
            with_toc=True,
            book_meta=BookMeta(title="Detected title"),
            language="en",
        )
        return None, None, None, None, "metering"


def _source_extraction(root: Path, *, with_toc: bool = False) -> PDFCraftExtraction:
    extraction = make_extraction(
        root, page_pixel_sizes={1: (10, 10)}, with_toc=with_toc
    )
    chapter = Chapter(
        None,
        -1,
        [TextFlowItem("body", 0, [SourceTextFragment(1, 1, (1, 1, 5, 5), ["original"])])],
    )
    save_xml(encode(chapter), root / "chapters" / "chapter_head.xml")
    return extraction._validate()


class _Upper:
    def transform(self, chapter: Chapter) -> Chapter:
        layout = chapter.flow_items[0]
        assert isinstance(layout, TextFlowItem)
        layout.children[0].content = ["translated"]
        return chapter


class _Identity:
    def transform(self, chapter: Chapter) -> Chapter:
        return chapter


class _PrefixXMLTranslator:
    def translate_element(self, task, **_kwargs):
        for element in task.element.iter():
            if element.text:
                element.text = f"translated:{element.text}"
        return task.element, task.payload


class TestPDFCraft(unittest.TestCase):
    def test_translate_extraction_is_the_public_translation_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")
            target_path = root / "target.pcex"

            target = PDFCraft().translate_extraction(
                source, target_path, _Upper(),
                translation_id="test-en", target_language="en",
            )

            self.assertTrue(target_path.is_file())
            self.assertEqual(target._page_pixel_sizes(), {1: (10, 10)})
            with target._materialize() as paths:
                self.assertIn("original", (paths.chapters / "chapter_head.xml").read_text())
                layer = paths.translations / "test-en"
                self.assertIn("translated", (layer / "chapters/chapter_head.xml").read_text())
                coverage = fromstring((layer / "coverage.xml").read_text(encoding="utf-8"))
                paragraph = coverage.find("narrative/paragraph")
                self.assertIsNotNone(paragraph)
                assert paragraph is not None
                self.assertEqual(paragraph.attrib, {
                    "chapter_id": "head", "page_index": "1", "order": "1", "state": "translated",
                })
            self.assertFalse(hasattr(PDFCraft, "translate_package"))
            self.assertFalse(hasattr(PDFCraft, "translate_furnitures"))

    def test_translate_extraction_composes_furniture_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")
            (root / "source" / "furnitures.xml").write_text(
                "<furnitures><patterns><pattern id='1' kind='universal'>"
                "<position id='0'>Header</position></pattern></patterns><pages>"
                "<page index='1'><section det='1,6,9,9'><association kind='universal' "
                "pattern_id='1' position_id='0'/></section></page>"
                "</pages></furnitures>", encoding="utf-8",
            )
            source = PDFCraftExtraction._from_workspace(root / "source")._validate()
            transformer = ChapterXMLTransformer(_PrefixXMLTranslator())

            without = PDFCraft().translate_extraction(
                source, root / "without.pcex", transformer,
                translation_id="without", target_language="en",
            )
            with without._materialize() as paths:
                layer = paths.translations / "without"
                self.assertIsNone(fromstring((layer / "coverage.xml").read_text(encoding="utf-8")).find("furnitures"))
                self.assertIn("Header", paths.furnitures.read_text(encoding="utf-8"))
                self.assertFalse((layer / "furnitures.xml").exists())

            with_furniture = PDFCraft().translate_extraction(
                source, root / "with.pcex", transformer, with_furniture=True,
                translation_id="with-en", target_language="en",
            )
            with with_furniture._materialize() as paths:
                self.assertIn("Header", paths.furnitures.read_text(encoding="utf-8"))
                layer = paths.translations / "with-en"
                self.assertIn("translated:Header", (layer / "furnitures.xml").read_text(encoding="utf-8"))
                coverage = fromstring((layer / "coverage.xml").read_text(encoding="utf-8"))
                position = coverage.find("furnitures/position")
                self.assertIsNotNone(position)
                assert position is not None
                self.assertEqual(position.get("state"), "translated")

    def test_translate_extraction_with_furniture_without_source_layer_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")

            translated = PDFCraft().translate_extraction(
                source, root / "target.pcex", ChapterXMLTransformer(_PrefixXMLTranslator()),
                with_furniture=True,
            )

            with translated._materialize() as paths:
                self.assertFalse(paths.furnitures.exists())
                info = PDFCraft().list_translations(translated)[0]
                self.assertTrue((paths.translations / info.id / "coverage.xml").exists())

    def test_translate_extraction_requires_xml_adapter_for_furniture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")

            with self.assertRaisesRegex(ValueError, "requires a ChapterXMLTransformer"):
                PDFCraft().translate_extraction(
                    source, root / "target.pcex", _Upper(), with_furniture=True,
                )

    def test_translate_pdf_uses_materialized_translation_for_furniture(self):
        with tempfile.TemporaryDirectory() as directory:
            extraction = _source_extraction(Path(directory) / "source")
            craft = PDFCraft()
            with patch.object(
                AsyncPDFCraft, "_translate_to_workspace",
                new_callable=AsyncMock, return_value=extraction,
            ) as translate, patch.object(
                FurnitureExtractionTransformer, "_transform_to_workspace_async",
                new_callable=AsyncMock, return_value=extraction,
            ) as furniture, patch.object(
                AsyncPDFCraft, "patch_pdf_with_extraction", new_callable=AsyncMock,
            ) as patch_pdf:
                craft.translate_pdf(
                    "source.pdf", extraction, "target.pdf",
                    ChapterXMLTransformer(_PrefixXMLTranslator()), with_furniture=True,
                )
            translate.assert_awaited_once()
            furniture.assert_awaited_once()
            patch_pdf.assert_awaited_once()

    def test_patch_pdf_with_extraction_delegates_to_pdf_patch_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            extraction = _source_extraction(Path(directory) / "source")
            with patch(
                "pdf_craft.craft.QT_DOMAIN.run", new_callable=AsyncMock,
            ) as run:
                PDFCraft().patch_pdf_with_extraction("source.pdf", extraction, "target.pdf")
            run.assert_awaited_once()
            self.assertFalse(run.call_args.args[-1])

    def test_patch_pdf_with_extraction_defers_page_validation_when_ignoring_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            extraction = _source_extraction(Path(directory) / "source")
            with patch(
                "pdf_craft.craft.QT_DOMAIN.run", new_callable=AsyncMock,
            ) as run:
                PDFCraft().patch_pdf_with_extraction(
                    "source.pdf", extraction, "target.pdf", ignore_errors=True,
                )
            run.assert_awaited_once()
            self.assertTrue(run.call_args.args[-1])

    def test_extraction_transform_creates_independent_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source", with_toc=True)
            target = ChapterExtractionTransformer(_Upper())._transform_blocking(
                source, root / "target.pcex"
            )

            with source._materialize() as paths:
                self.assertIn("original", (paths.chapters / "chapter_head.xml").read_text())
            with target._materialize() as paths:
                self.assertIn("translated", (paths.chapters / "chapter_head.xml").read_text())
                self.assertTrue(paths.toc.is_file())

    def test_extraction_transform_preserves_furnitures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")
            save_xml(fromstring(
                '<furnitures><patterns/><pages><page index="1">'
                '<section det="1,1,5,5">Header</section></page></pages></furnitures>'
            ), root / "source" / "furnitures.xml")
            source = PDFCraftExtraction._from_workspace(root / "source")._validate()

            target = ChapterExtractionTransformer(_Identity())._transform_blocking(
                source, root / "target.pcex"
            )
            with target._materialize() as paths:
                self.assertTrue(paths.furnitures.is_file())
                self.assertIn("Header", paths.furnitures.read_text(encoding="utf-8"))

    def test_extraction_toc_transform_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source", with_toc=True)

            def translate_toc(element):
                element.set("translated", "yes")
                return element

            target = asyncio.run(ChapterExtractionTransformer(
                _Identity(), toc_transformer=translate_toc
            ).transform(source, root / "target.pcex"))
            with target._materialize() as paths:
                self.assertIn('translated="yes"', paths.toc.read_text(encoding="utf-8"))

    def test_epub_only_facade_needs_no_pdf_options(self):
        craft = PDFCraft()
        with patch(
            "pdf_craft.craft.run_epub_translation", new_callable=AsyncMock,
        ) as translate:
            craft.translate_epub(
                "source.epub", "target.epub", target_language="zh",
                submit=SubmitKind.REPLACE,
            )
        translate.assert_awaited_once()

    def test_pdf_extraction_requires_options_only_when_used(self):
        with self.assertRaisesRegex(ValueError, "PDFOptions"):
            PDFCraft().extract_pdf("source.pdf", "book.pcex")

    def test_extraction_options_reach_extractor_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = _Engine()
            refinement = FootnoteRefinement(
                jev=JEV("jev-key"),
                llm=LLM("llm-key", "https://example.invalid/v1", "model", "o200k_base"),
            )
            extraction, metering = PDFCraft.from_engine(engine).extract_pdf_with_metering(
                "source.pdf",
                root / "book.pcex",
                ExtractionOptions(
                    page_indexes=(2, 4), max_ocr_tokens=12,
                    footnotes=FootnoteOptions(refinement=refinement),
                ),
                analysing_path=root / "analysis",
            )
            self.assertEqual(metering, "metering")
            assert engine.kwargs is not None
            self.assertEqual(engine.kwargs["page_indexes"], (2, 4))
            self.assertEqual(engine.kwargs["max_tokens"], 12)
            self.assertTrue(engine.kwargs["includes_furniture"])
            self.assertTrue(engine.kwargs["includes_footnotes"])
            self.assertIs(engine.kwargs["footnote_refinement"], refinement)
            extraction._validate(require_toc=True)
            self.assertTrue((root / "analysis" / "extraction").is_dir())

    def test_algorithmic_footnotes_need_no_model_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine()
            PDFCraft.from_engine(engine).extract_pdf(
                "source.pdf",
                Path(directory) / "book.pcex",
                ExtractionOptions(footnotes=FootnoteOptions()),
            )

        assert engine.kwargs is not None
        self.assertTrue(engine.kwargs["includes_footnotes"])
        self.assertIsNone(engine.kwargs["footnote_refinement"])

    def test_public_extraction_requires_pcex_output(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine()
            with self.assertRaisesRegex(ValueError, "\\.pcex"):
                PDFCraft.from_engine(engine).extract_pdf(
                    "source.pdf", Path(directory) / "book"
                )
            self.assertIsNone(engine.kwargs)
        self.assertFalse(hasattr(PDFExtractor, "extract_to_workspace"))
        self.assertFalse(hasattr(ChapterExtractionTransformer, "transform_to_workspace"))

    def test_rendering_extraction_does_not_construct_pdf_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = _source_extraction(root / "source")
            with patch(
                "pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock,
            ) as render:
                PDFCraft().render_markdown(extraction, root / "book.md")
            render.assert_awaited_once()

    def test_one_shot_workflow_uses_workspace_without_zip_churn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            craft = PDFCraft.from_engine(_Engine())
            with patch.object(PDFCraftExtraction, "_export_async", new_callable=AsyncMock) as export, \
                    patch("pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock) as render:
                result = craft.convert_pdf_to_markdown(
                    "source.pdf", root / "book.md", analysing_path=root / "analysis"
                )
            self.assertEqual(result, "metering")
            export.assert_not_awaited()
            rendered_extraction = render.call_args.args[0]
            with rendered_extraction._materialize() as paths:
                self.assertEqual(paths.root, root / "analysis" / "extraction")

    def test_one_shot_workflow_can_also_export_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            craft = PDFCraft.from_engine(_Engine())
            with patch("pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock), \
                    patch("pdf_craft.document.package._extract_archive") as unpack:
                craft.convert_pdf_to_markdown(
                    "source.pdf",
                    root / "book.md",
                    analysing_path=root / "analysis",
                    extraction_path=root / "book.pcex",
                )
            self.assertTrue((root / "book.pcex").is_file())
            unpack.assert_not_called()

    def test_one_shot_markdown_cleans_implicit_analysis_workspace(self):
        engine = _Engine()
        craft = PDFCraft.from_engine(engine)
        with patch("pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock):
            result = craft.convert_pdf_to_markdown("source.pdf", "book.md")
        self.assertEqual(result, "metering")
        assert engine.analysing_path is not None
        self.assertFalse(engine.analysing_path.exists())

    def test_markdown_and_epub_conversion_disable_furniture_extraction(self):
        for method, output in (("convert_pdf_to_markdown", "book.md"), ("convert_pdf_to_epub", "book.epub")):
            with self.subTest(method=method):
                engine = _Engine()
                craft = PDFCraft.from_engine(engine)
                with patch("pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock), \
                        patch("pdf_craft.craft.EpubRenderer.render", new_callable=AsyncMock):
                    getattr(craft, method)(
                        "source.pdf", output,
                        extraction=ExtractionOptions(includes_furniture=True),
                    )
                assert engine.kwargs is not None
                self.assertFalse(engine.kwargs["includes_furniture"])

    def test_epub_uses_manifest_metadata_without_rereading_source_pdf(self):
        craft = PDFCraft.from_engine(_Engine())
        observed = {}

        async def inspect_manifest(extraction, _output, **kwargs):
            observed["title"] = extraction._book_meta().title
            observed["language"] = extraction._language()
            observed["book_meta"] = kwargs["book_meta"]
            observed["lan"] = kwargs["lan"]

        with patch(
            "pdf_craft.craft.EpubRenderer.render",
            new_callable=AsyncMock, side_effect=inspect_manifest,
        ):
            craft.convert_pdf_to_epub("source.pdf", "book.epub")
        self.assertEqual(observed["title"], "Detected title")
        self.assertEqual(observed["language"], "en")
        self.assertIsNone(observed["book_meta"])
        self.assertIsNone(observed["lan"])

    def test_epub_conversion_forwards_translation_events(self):
        craft = PDFCraft.from_engine(_Engine())
        callback = Mock()
        translator = Mock()
        with patch.object(
            AsyncPDFCraft, "_translate_to_workspace",
            new_callable=AsyncMock,
            side_effect=lambda extraction, *_args, **_kwargs: extraction,
        ) as translate, patch(
            "pdf_craft.craft.EpubRenderer.render", new_callable=AsyncMock,
        ):
            craft.convert_pdf_to_epub(
                "source.pdf", "book.epub", translator=translator,
                on_translation_event=callback,
            )
        self.assertIs(translate.call_args.kwargs["on_translation_event"], callback)

    def test_markdown_workflow_forwards_aborted_to_renderer(self):
        craft = PDFCraft.from_engine(_Engine())
        stopped = lambda: False
        with patch(
            "pdf_craft.craft.MarkdownRenderer.render", new_callable=AsyncMock,
        ) as render:
            craft.convert_pdf_to_markdown(
                "source.pdf", "book.md", extraction=ExtractionOptions(aborted=stopped)
            )
        self.assertIs(render.call_args.kwargs["aborted"], stopped)

    def test_pdf_options_are_accepted_without_eager_pdf_initialization(self):
        PDFCraft(pdf=PDFOptions())
