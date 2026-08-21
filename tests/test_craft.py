import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree.ElementTree import tostring

from pdf_craft.craft import ExtractionOptions, PDFCraft, PDFOptions, TranslationStep
from pdf_craft.document import DocumentPackage
from pdf_craft.error import InterruptedError as PDFInterruptedError, PDFError
from pdf_craft.metering import InterruptedKind, OCRTokensMetering
from pdf_craft.sequence.chapter import BlockLayout, Chapter, ParagraphLayout, encode
from pdf_craft.transform import Transform
from pdf_craft.transformer import ChapterPackageTransformer, SubmitKind


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
    def test_package_step_creates_independent_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DocumentPackage.from_path(root / "source")
            source.chapters_path.mkdir(parents=True)
            source.assets_path.mkdir()
            assert source.toc_path is not None
            source.toc_path.write_text("<toc page_indexes=\"1\" />")
            source.write_metadata(page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(
                None, -1, [ParagraphLayout(
                    "text", 0, [BlockLayout(1, 1, (1, 1, 5, 5), ["original"])]
                )]
            )
            (source.chapters_path / "chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(chapter), encoding="unicode")
            )

            class Upper:
                def transform(self, chapter: Chapter) -> Chapter:
                    layout = chapter.layouts[0]
                    assert isinstance(layout, ParagraphLayout)
                    layout.blocks[0].content = ["translated"]
                    return chapter

            target = ChapterPackageTransformer(Upper()).transform(source, root / "target")
            self.assertEqual(target.page_pixel_sizes(), {1: (10, 10)})
            self.assertIn("original", (source.chapters_path / "chapter_1.xml").read_text())
            self.assertIn("translated", (target.chapters_path / "chapter_1.xml").read_text())
            assert target.toc_path is not None
            self.assertEqual(source.toc_path.read_text(), target.toc_path.read_text())

    def test_package_toc_transform_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DocumentPackage.from_path(root / "source")
            source.chapters_path.mkdir(parents=True)
            source.assets_path.mkdir()
            assert source.toc_path is not None
            source.toc_path.write_text('<toc page_indexes="1"><item /></toc>')
            source.write_metadata(page_pixel_sizes={1: (10, 10)})

            class Identity:
                def transform(self, chapter: Chapter) -> Chapter:
                    return chapter

            def translate_toc(element):
                element.set("translated", "yes")
                return element

            target = ChapterPackageTransformer(
                Identity(), toc_transformer=translate_toc
            ).transform(source, root / "target")
            assert target.toc_path is not None
            self.assertIn('translated="yes"', target.toc_path.read_text())

    def test_pdf_rejects_append_block_steps_before_transforming(self):
        craft = PDFCraft.from_engine(_Engine())
        step = TranslationStep(Mock(), SubmitKind.APPEND_BLOCK)
        with self.assertRaisesRegex(ValueError, "APPEND_BLOCK"):
            craft.translate_pdf(
                "source.pdf", DocumentPackage(Path("chapters"), Path("assets")),
                "out.pdf", lambda text: text, steps=[step]
            )

    def test_pdf_rejects_append_block_package_transformer(self):
        craft = PDFCraft.from_engine(_Engine())
        transformer = ChapterPackageTransformer(Mock(), mode=SubmitKind.APPEND_BLOCK)
        with self.assertRaisesRegex(ValueError, "APPEND_BLOCK"):
            craft.translate_pdf(
                "source.pdf", DocumentPackage(Path("chapters"), Path("assets")),
                "out.pdf", lambda text: text, steps=[transformer]
            )

    def test_pdf_rejects_append_block_custom_package_step(self):
        class CustomPackageTransformer:
            def transform(self, package: DocumentPackage, output_path: Path) -> DocumentPackage:
                del output_path
                return package

        craft = PDFCraft.from_engine(_Engine())
        with self.assertRaisesRegex(ValueError, "APPEND_BLOCK"):
            craft.translate_pdf(
                "source.pdf", DocumentPackage(Path("chapters"), Path("assets")),
                "out.pdf", lambda text: text,
                steps=[TranslationStep(CustomPackageTransformer(), SubmitKind.APPEND_BLOCK)],
            )

    def test_optional_chapter_transformer_is_not_treated_as_package_transformer(self):
        class OptionalChapterTransformer:
            def transform(self, chapter: Chapter, *, trace: bool = False) -> Chapter:
                del trace
                return chapter

        step = TranslationStep(OptionalChapterTransformer())
        transformer = getattr(PDFCraft, "_as_package_transformer")(step)
        self.assertIsInstance(transformer, ChapterPackageTransformer)

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
        error = PDFInterruptedError(InterruptedKind.ABORT, OCRTokensMetering(1, 2))
        facade = Mock()
        facade.convert_pdf_to_epub.side_effect = error
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            with self.assertRaises(PDFInterruptedError) as raised:
                Transform().transform_epub("source.pdf", "book.epub")
        self.assertIs(raised.exception, error)

    def test_legacy_markdown_uses_output_directory_for_default_assets(self):
        facade = Mock()
        with patch("pdf_craft.craft.PDFCraft.from_engine", return_value=facade):
            Transform().transform_markdown("source.pdf", "output/book.md")
        self.assertEqual(facade.convert_pdf_to_markdown.call_args.kwargs["assets_path"], Path("output/assets"))
