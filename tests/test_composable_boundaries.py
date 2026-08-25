import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import cast
from xml.etree.ElementTree import tostring
from PIL import Image

from pdf_craft.document import DocumentPackage
from pdf_craft.error import NoUsableOCRPagesError, OCRError
from pdf_craft.extractor import PDFExtractor
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.pipeline.pdf import PDFPatcher
from pdf_craft.transformer import ChapterPackageTransformer, ChapterXMLTransformer
from pdf_craft.renderer import EpubRenderer, MarkdownRenderer
from pdf_craft.extractor.chapter.chapter import BlockLayout, Chapter, InlineExpression, ParagraphLayout, Reference, encode
from pdf_craft.expression import ExpressionKind
from pdf_craft.ocr_config import DeepSeekOCRLocalConfig
from pdf_craft.pdf.ocr import OCR, OCREvent, OCREventKind
from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pdf.types import Page
from pdf_craft.transform import PDFExtractionEngine


class _FakeTransform:
    def extract_package(self, *, analysing_path, **_kwargs):
        (analysing_path / "chapters").mkdir(parents=True)
        (analysing_path / "assets").mkdir()
        (analysing_path / "toc.xml").write_text("<toc/>")
        DocumentPackage.from_path(analysing_path).write_metadata(
            dpi=300, page_pixel_sizes={1: (100, 100)}
        )
        return None, None, None, None, "metering"


class _NoAssetTransform:
    def extract_package(self, *, analysing_path, **_kwargs):
        (analysing_path / "chapters").mkdir(parents=True)
        DocumentPackage.from_path(analysing_path).write_metadata(
            dpi=300, page_pixel_sizes={1: (100, 100)}
        )
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

    def test_package_translation_skips_empty_chapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DocumentPackage.from_path(root / "source")
            source.chapters_path.mkdir(parents=True)
            source.assets_path.mkdir()
            source.write_metadata(page_pixel_sizes={1: (100, 100)})
            empty = Chapter(None, 0, [])
            text = Chapter(None, 0, [ParagraphLayout(
                "text", 0, [BlockLayout(1, 1, (1, 1, 50, 50), ["text"])]
            )])
            (source.chapters_path / "chapter_1.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(empty), encoding="unicode")
            )
            (source.chapters_path / "chapter_2.xml").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                + tostring(encode(text), encoding="unicode")
            )

            translator = _DeterministicXMLTranslator()
            target = ChapterPackageTransformer(
                ChapterXMLTransformer(translator)
            ).transform(source, root / "target")

            self.assertEqual(translator.calls, 1)
            self.assertEqual(
                (target.chapters_path / "chapter_1.xml").read_text(),
                (source.chapters_path / "chapter_1.xml").read_text(),
            )
            self.assertIn("T:text", (target.chapters_path / "chapter_2.xml").read_text())

    def test_extractor_creates_empty_assets_directory_for_asset_free_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, _ = PDFExtractor(_NoAssetTransform()).extract_with_metering(
                root / "input.pdf", root / "package"
            )
            self.assertTrue(package.assets_path.is_dir())

    def test_extractor_produces_package_consumed_by_renderers_without_ocr_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, metering = PDFExtractor(_FakeTransform()).extract_with_metering(root / "input.pdf", root / "package")
            self.assertEqual(metering, "metering")
            self.assertFalse((root / "package" / "ocr").exists())
            self.assertIsNone(package.cover_path)
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

    def test_metadata_path_is_retained_for_direct_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage(root / "chapters", root / "assets")
            package.write_metadata(dpi=300, page_pixel_sizes={1: (30, 30)})
            self.assertEqual(package.page_pixel_sizes(), {1: (30, 30)})

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

    def test_package_rejects_malformed_page_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage.from_path(root)
            package.chapters_path.mkdir()
            package.assets_path.mkdir()
            assert package.metadata_path is not None
            package.metadata_path.write_text('{"schema": 1, "page_pixel_sizes": {"1": [1]}}')
            with self.assertRaisesRegex(ValueError, "page_pixel_sizes"):
                package.validate()
            package.metadata_path.write_text('{"schema": 1, "page_pixel_sizes": {"1": [1.5, 2]}}')
            with self.assertRaisesRegex(ValueError, "page_pixel_sizes"):
                package.validate()

    def test_ocr_rejects_malformed_geometry_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ocr_path = root / "ocr"
            ocr_path.mkdir()
            (ocr_path / "page_pixel_sizes.json").write_text('{"1": [1.5, 100]}')
            ocr = OCR(DeepSeekOCRLocalConfig(local_only=True), cast(PDFHandler, _FakeHandler()))
            with self.assertRaisesRegex(ValueError, "geometry cache"):
                list(ocr.recognize(root / "input.pdf", root / "assets", ocr_path))
