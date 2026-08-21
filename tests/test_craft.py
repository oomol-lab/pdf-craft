import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pdf_craft.craft import ExtractionOptions, PDFCraft, PDFOptions
from pdf_craft.document import DocumentPackage
from pdf_craft.error import InterruptedError, PDFError
from pdf_craft.metering import InterruptedKind, OCRTokensMetering
from pdf_craft.transform import Transform
from pdf_craft.transformer import SubmitKind


class _Engine:
    def __init__(self):
        self.kwargs = None
        self.metadata_source = None

    def extract_package(self, *, analysing_path, **kwargs):
        self.kwargs = kwargs
        (analysing_path / "chapters").mkdir(parents=True)
        (analysing_path / "assets").mkdir()
        (analysing_path / "toc.xml").write_text("<toc/>")
        DocumentPackage.from_path(analysing_path).write_metadata(page_pixel_sizes={1: (10, 10)})
        return None, None, None, None, "metering"

    def _extract_book_meta(self, source):
        self.metadata_source = source
        return "detected metadata"


class TestPDFCraft(unittest.TestCase):
    def test_epub_only_facade_needs_no_pdf_options(self):
        craft = PDFCraft()
        with patch("pdf_craft.craft.run_epub_translation") as translate:
            craft.translate_epub("source.epub", "target.epub", target_language="zh", submit=SubmitKind.REPLACE)
        translate.assert_called_once()

    def test_pdf_extraction_requires_options_only_when_used(self):
        with self.assertRaisesRegex(ValueError, "PDFOptions"):
            PDFCraft().extract_pdf("source.pdf", "package")

    def test_extraction_options_reach_extractor_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine()
            package, metering = PDFCraft.from_engine(engine).extract_pdf_with_metering(
                "source.pdf", Path(directory), ExtractionOptions(page_indexes=(2, 4), max_ocr_tokens=12)
            )
            self.assertEqual(metering, "metering")
            assert engine.kwargs is not None
            self.assertEqual(engine.kwargs["page_indexes"], (2, 4))
            self.assertEqual(engine.kwargs["max_tokens"], 12)
            self.assertTrue(package.has_toc())

    def test_rendering_package_does_not_construct_pdf_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage.from_path(root)
            package.chapters_path.mkdir()
            package.assets_path.mkdir()
            with patch("pdf_craft.craft.MarkdownRenderer.render") as render:
                PDFCraft().render_markdown(package, root / "book.md")
            render.assert_called_once()

    def test_one_shot_workflow_uses_public_steps(self):
        craft = PDFCraft.from_engine(_Engine())
        with patch.object(craft, "extract_pdf_with_metering", return_value=(object(), "metering")) as extract, \
             patch.object(craft, "render_markdown") as render:
            result = craft.convert_pdf_to_markdown("source.pdf", "book.md", package_path="package")
        self.assertEqual(result, "metering")
        extract.assert_called_once()
        render.assert_called_once()

    def test_epub_workflow_detects_metadata_and_forwards_aborted(self):
        engine = _Engine()
        craft = PDFCraft.from_engine(engine)
        stopped = lambda: False
        with patch.object(craft, "extract_pdf_with_metering", return_value=(object(), "metering")), \
             patch.object(craft, "render_epub") as render:
            result = craft.convert_pdf_to_epub(
                "source.pdf", "book.epub", package_path="package",
                extraction=ExtractionOptions(aborted=stopped),
            )
        self.assertEqual(result, "metering")
        self.assertEqual(engine.metadata_source, Path("source.pdf"))
        self.assertEqual(render.call_args.kwargs["book_meta"], "detected metadata")
        self.assertIs(render.call_args.kwargs["aborted"], stopped)

    def test_markdown_workflow_forwards_aborted_to_renderer_step(self):
        craft = PDFCraft.from_engine(_Engine())
        stopped = lambda: False
        with patch.object(craft, "extract_pdf_with_metering", return_value=(object(), "metering")), \
             patch.object(craft, "render_markdown") as render:
            craft.convert_pdf_to_markdown(
                "source.pdf", "book.md", package_path="package",
                extraction=ExtractionOptions(aborted=stopped),
            )
        self.assertIs(render.call_args.kwargs["aborted"], stopped)

    def test_pdf_options_are_accepted_without_eager_pdf_initialization(self):
        PDFCraft(pdf=PDFOptions())

    def test_legacy_transform_wraps_facade_errors_for_both_outputs(self):
        facade = Mock()
        facade.convert_pdf_to_markdown.side_effect = ValueError("broken")
        facade.convert_pdf_to_epub.side_effect = ValueError("broken")
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            transform = Transform()
            with self.assertRaisesRegex(RuntimeError, "transform source.pdf to markdown failed"):
                transform.transform_markdown("source.pdf", "book.md")
            with self.assertRaisesRegex(RuntimeError, "transform source.pdf to epub failed"):
                transform.transform_epub("source.pdf", "book.epub")

    def test_legacy_transform_preserves_inline_pdf_error(self):
        facade = Mock()
        facade.convert_pdf_to_markdown.side_effect = PDFError("page failed", page_index=1)
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            with self.assertRaises(PDFError):
                Transform().transform_markdown("source.pdf", "book.md")

    def test_legacy_transform_preserves_public_interrupted_error(self):
        error = InterruptedError(InterruptedKind.ABORT, OCRTokensMetering(1, 2))
        facade = Mock()
        facade.convert_pdf_to_epub.side_effect = error
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            with self.assertRaises(InterruptedError) as raised:
                Transform().transform_epub("source.pdf", "book.epub")
        self.assertIs(raised.exception, error)

    def test_legacy_markdown_uses_output_directory_for_default_assets(self):
        facade = Mock()
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            Transform().transform_markdown("source.pdf", "output/book.md")
        self.assertEqual(facade.convert_pdf_to_markdown.call_args.kwargs["assets_path"], Path("output/assets"))
