import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import cast

from pdf_craft.document import DocumentPackage
from pdf_craft.extractor import PDFExtractor
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.pipeline.pdf import PDFPatcher
from pdf_craft.transformer import ChapterXMLTransformer
from pdf_craft.renderer import EpubRenderer, MarkdownRenderer
from pdf_craft.sequence.chapter import BlockLayout, Chapter, InlineExpression, ParagraphLayout, Reference
from pdf_craft.expression import ExpressionKind
from pdf_craft.pdf.ocr import OCR
from pdf_craft.pdf.handler import PDFHandler


class _FakeTransform:
    def extract_package(self, *, analysing_path, **_kwargs):
        (analysing_path / "chapters").mkdir(parents=True)
        (analysing_path / "assets").mkdir()
        (analysing_path / "toc.xml").write_text("<toc/>")
        DocumentPackage.from_path(analysing_path).write_metadata(
            dpi=300, page_pixel_sizes={1: (100, 100)}
        )
        return None, None, None, None, "metering"


class _CapturePatcher:
    def __init__(self):
        self.replacements = []

    def patch(self, _source, _target, replacements):
        self.replacements = list(replacements)


class _FakeDocument:
    def render_page(self, _page_index, _dpi):
        class Image:
            size = (100, 100)
        return Image()

    def close(self):
        pass


class _FakeHandler:
    def open(self, _path):
        return _FakeDocument()


class _SizedDocument:
    pages_count: int = 1

    def page_size(self, _index):
        return (10.0, 10.0)

    def render_page(self, _page_index, dpi):
        class Image:
            size = (dpi * 10, dpi * 10)
        return Image()

    def close(self):
        pass


class _SizedHandler:
    def open(self, _path):
        return _SizedDocument()


class _DeterministicXMLTranslator:
    def translate_element(self, task, **_kwargs):
        for node in task.element.iter():
            if node.text:
                node.text = "T:" + node.text
            if node.tail:
                node.tail = "T:" + node.tail
        return task.element, task.payload


class TestComposableBoundaries(unittest.TestCase):
    def test_extractor_produces_package_consumed_by_renderers_without_ocr_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, metering = PDFExtractor(_FakeTransform()).extract_with_metering(root / "input.pdf", root / "package")
            self.assertEqual(metering, "metering")
            self.assertFalse((root / "package" / "ocr").exists())
            self.assertEqual(package.page_pixel_sizes(), {1: (100, 100)})
            with patch("pdf_craft.renderer.markdown.renderer.render_markdown_file") as markdown:
                MarkdownRenderer().render(package, root / "book.md")
            self.assertEqual(markdown.call_args.args[0], package.chapters_path)
            with patch("pdf_craft.renderer.epub.renderer.render_epub_file") as epub:
                EpubRenderer().render(package, root / "book.epub")
            self.assertEqual(epub.call_args.args[0], package.chapters_path)

    def test_pdf_pipeline_preserves_structured_content_and_uses_package_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage.from_path(root)
            package.chapters_path.mkdir(parents=True)
            package.assets_path.mkdir()
            package.write_metadata(dpi=300, page_pixel_sizes={1: (100, 100)})
            reference = Reference(1, 2, "[1]", [])
            chapter = Chapter(None, -1, [
                ParagraphLayout("text", 0, [BlockLayout(
                    1, 1, (1, 1, 50, 50), ["text ", InlineExpression(ExpressionKind.INLINE_DOLLAR, "x"), reference]
                )]),
                ParagraphLayout("sub_title", 1, [BlockLayout(1, 2, (1, 50, 50, 90), ["heading"])]),
            ])
            patcher = _CapturePatcher()
            with patch("pdf_craft.pipeline.pdf.pipeline.create_chapters_reader", return_value=lambda: iter([chapter])):
                PDFTranslationPipeline(patcher=cast(PDFPatcher, patcher)).translate(
                    root / "input.pdf", root / "out.pdf", package,
                    ChapterXMLTransformer(_DeterministicXMLTranslator())
                )
            self.assertEqual(len(patcher.replacements), 2)
            replacement = patcher.replacements[0]
            self.assertEqual(replacement.page_pixel_size, (100, 100))
            self.assertIn("$T:x$", replacement.text)
            self.assertIn("[1]", replacement.text)
            self.assertIn("T:heading", patcher.replacements[1].text)

    def test_metadata_path_is_retained_for_direct_package_and_ocr_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage(root / "chapters", root / "assets")
            package.write_metadata(dpi=300, page_pixel_sizes={1: (30, 30)})
            self.assertEqual(package.page_pixel_sizes(), {1: (30, 30)})
            ocr = object.__new__(OCR)
            with patch.object(OCR, "_get_pdf_handler", return_value=cast(PDFHandler, _SizedHandler())):
                self.assertEqual(ocr.page_pixel_sizes(root / "input.pdf", 300, 15_000), {1: (100, 100)})

    def test_epub_renderer_rejects_unsupported_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage.from_path(root)
            package.chapters_path.mkdir()
            package.assets_path.mkdir()
            assert package.toc_path is not None
            package.toc_path.write_text("<toc/>")
            with self.assertRaises(ValueError):
                EpubRenderer().render(package, root / "book.epub", lan="fr")  # type: ignore[arg-type]
