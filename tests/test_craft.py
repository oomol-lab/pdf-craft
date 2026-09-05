# pylint: disable=protected-access

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from epub_generator import BookMeta

from pdf_craft.craft import ExtractionOptions, PDFCraft, PDFOptions
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor import PDFExtractor
from pdf_craft.extractor.chapter.chapter import BlockLayout, Chapter, ParagraphLayout, encode
from pdf_craft.common import save_xml
from pdf_craft.transformer import ChapterExtractionTransformer, SubmitKind
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
        [ParagraphLayout("text", 0, [BlockLayout(1, 1, (1, 1, 5, 5), ["original"])])],
    )
    save_xml(encode(chapter), root / "chapters" / "chapter_1.xml")
    return extraction.validate()


class _Upper:
    def transform(self, chapter: Chapter) -> Chapter:
        layout = chapter.layouts[0]
        assert isinstance(layout, ParagraphLayout)
        layout.blocks[0].content = ["translated"]
        return chapter


class _Identity:
    def transform(self, chapter: Chapter) -> Chapter:
        return chapter


class TestPDFCraft(unittest.TestCase):
    def test_translate_extraction_is_the_public_translation_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source")
            target_path = root / "target.pcex"

            target = PDFCraft().translate_extraction(source, target_path, _Upper())

            self.assertTrue(target_path.is_file())
            self.assertEqual(target.page_pixel_sizes(), {1: (10, 10)})
            with target._materialize() as paths:
                self.assertIn("translated", (paths.chapters / "chapter_1.xml").read_text())
            self.assertFalse(hasattr(PDFCraft, "translate_package"))

    def test_patch_pdf_with_extraction_delegates_to_pdf_patch_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            extraction = _source_extraction(Path(directory) / "source")
            with patch("pdf_craft.craft._validate_extraction_for_pdf") as validate, \
                    patch("pdf_craft.craft.PDFTranslationPipeline.patch") as patch_pdf:
                PDFCraft().patch_pdf_with_extraction("source.pdf", extraction, "target.pdf")
            validate.assert_called_once()
            patch_pdf.assert_called_once()

    def test_extraction_transform_creates_independent_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source", with_toc=True)
            target = ChapterExtractionTransformer(_Upper()).transform(
                source, root / "target.pcex"
            )

            with source._materialize() as paths:
                self.assertIn("original", (paths.chapters / "chapter_1.xml").read_text())
            with target._materialize() as paths:
                self.assertIn("translated", (paths.chapters / "chapter_1.xml").read_text())
                self.assertTrue(paths.toc.is_file())

    def test_extraction_toc_transform_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source_extraction(root / "source", with_toc=True)

            def translate_toc(element):
                element.set("translated", "yes")
                return element

            target = ChapterExtractionTransformer(
                _Identity(), toc_transformer=translate_toc
            ).transform(source, root / "target.pcex")
            with target._materialize() as paths:
                self.assertIn('translated="yes"', paths.toc.read_text(encoding="utf-8"))

    def test_epub_only_facade_needs_no_pdf_options(self):
        craft = PDFCraft()
        with patch("pdf_craft.craft.run_epub_translation") as translate:
            craft.translate_epub(
                "source.epub", "target.epub", target_language="zh",
                submit=SubmitKind.REPLACE,
            )
        translate.assert_called_once()

    def test_pdf_extraction_requires_options_only_when_used(self):
        with self.assertRaisesRegex(ValueError, "PDFOptions"):
            PDFCraft().extract_pdf("source.pdf", "book.pcex")

    def test_extraction_options_reach_extractor_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = _Engine()
            extraction, metering = PDFCraft.from_engine(engine).extract_pdf_with_metering(
                "source.pdf",
                root / "book.pcex",
                ExtractionOptions(page_indexes=(2, 4), max_ocr_tokens=12),
                analysing_path=root / "analysis",
            )
            self.assertEqual(metering, "metering")
            assert engine.kwargs is not None
            self.assertEqual(engine.kwargs["page_indexes"], (2, 4))
            self.assertEqual(engine.kwargs["max_tokens"], 12)
            extraction.validate(require_toc=True)
            self.assertTrue((root / "analysis" / "extraction").is_dir())

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
            with patch("pdf_craft.craft.MarkdownRenderer.render") as render:
                PDFCraft().render_markdown(extraction, root / "book.md")
            render.assert_called_once()

    def test_one_shot_workflow_uses_workspace_without_zip_churn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            craft = PDFCraft.from_engine(_Engine())
            with patch.object(PDFCraftExtraction, "export") as export, \
                    patch.object(craft, "render_markdown") as render:
                result = craft.convert_pdf_to_markdown(
                    "source.pdf", root / "book.md", analysing_path=root / "analysis"
                )
            self.assertEqual(result, "metering")
            export.assert_not_called()
            rendered_extraction = render.call_args.args[0]
            with rendered_extraction._materialize() as paths:
                self.assertEqual(paths.root, root / "analysis" / "extraction")

    def test_one_shot_workflow_can_also_export_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            craft = PDFCraft.from_engine(_Engine())
            with patch.object(craft, "render_markdown"), \
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
        with patch.object(craft, "render_markdown"):
            result = craft.convert_pdf_to_markdown("source.pdf", "book.md")
        self.assertEqual(result, "metering")
        assert engine.analysing_path is not None
        self.assertFalse(engine.analysing_path.exists())

    def test_epub_uses_manifest_metadata_without_rereading_source_pdf(self):
        craft = PDFCraft.from_engine(_Engine())
        observed = {}

        def inspect_manifest(extraction, _output, **kwargs):
            observed["title"] = extraction.book_meta().title
            observed["language"] = extraction.language()
            observed["book_meta"] = kwargs["book_meta"]
            observed["lan"] = kwargs["lan"]

        with patch.object(craft, "render_epub", side_effect=inspect_manifest):
            craft.convert_pdf_to_epub("source.pdf", "book.epub")
        self.assertEqual(observed["title"], "Detected title")
        self.assertEqual(observed["language"], "en")
        self.assertIsNone(observed["book_meta"])
        self.assertIsNone(observed["lan"])

    def test_epub_conversion_forwards_translation_events(self):
        craft = PDFCraft.from_engine(_Engine())
        callback = Mock()
        translator = Mock()
        with patch.object(craft, "_translate_to_workspace", return_value=Mock()) as translate, \
                patch.object(craft, "render_epub"):
            craft.convert_pdf_to_epub(
                "source.pdf", "book.epub", translator=translator,
                on_translation_event=callback,
            )
        self.assertIs(translate.call_args.kwargs["on_translation_event"], callback)

    def test_markdown_workflow_forwards_aborted_to_renderer(self):
        craft = PDFCraft.from_engine(_Engine())
        stopped = lambda: False
        with patch.object(craft, "render_markdown") as render:
            craft.convert_pdf_to_markdown(
                "source.pdf", "book.md", extraction=ExtractionOptions(aborted=stopped)
            )
        self.assertIs(render.call_args.kwargs["aborted"], stopped)

    def test_pdf_options_are_accepted_without_eager_pdf_initialization(self):
        PDFCraft(pdf=PDFOptions())
