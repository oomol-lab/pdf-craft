# pylint: disable=no-member,protected-access,c-extension-no-member

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import pypdf
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pipeline.pdf import (
    FillWindowPlan, FittedParagraph, PDFPatcher, PDFReplacement, PDFReplacementRegion, PatchTextOptions,
    QTextParagraphFiller,
)
from pdf_craft.pipeline.pdf.text_layout import _CJK_FONT_CANDIDATES, _ensure_qt_application


class TestPDFPatcher(unittest.TestCase):
    def test_automatic_font_scans_all_replacements_before_layout_in_either_input_order(self):
        """A later CJK title must influence the run-wide automatic family."""
        from PySide6 import QtGui

        _ensure_qt_application(QtGui)
        installed = {family.casefold(): family for family in QtGui.QFontDatabase.families()}
        cjk_font = next(
            (installed[candidate.casefold()] for candidate in _CJK_FONT_CANDIDATES
             if candidate.casefold() in installed),
            None,
        )
        if cjk_font is None:
            self.skipTest("this Qt installation has no preferred CJK candidate")

        body = PDFReplacement(1, (5, 5, 195, 75), "English body", (200, 200))
        title = PDFReplacement(
            1, (5, 80, 195, 195), "中文标题", (200, 200), layout_ref="sub_title",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(5, 5, "source")
            doc.save()

            for index, replacements in enumerate(((body, title), (title, body))):
                patcher = PDFPatcher(options=PatchTextOptions(max_font_size=8, min_font_size=8))
                patcher.patch(source, root / f"target-{index}.pdf", replacements)
                resolutions = patcher.font_resolutions
                self.assertEqual(len(resolutions), 1)
                self.assertEqual(resolutions[0].source, "automatic")
                self.assertEqual(resolutions[0].resolved_font_name, cjk_font)

    def test_samples_colored_background_from_the_source_page_handler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            doc.drawString(1, 1, "source")
            doc.save()
            document: Any = Mock()
            document.render_page.return_value = Image.new("RGB", (100, 100), (234, 220, 183))
            handler: Any = Mock()
            handler.open.return_value = document

            PDFPatcher(font_size=8, pdf_handler=handler).patch(
                source,
                target,
                [PDFReplacement(1, (20, 20, 80, 80), "translated", (100, 100))],
            )

            document.render_page.assert_called_once_with(1, 300)
            document.close.assert_called_once_with()
            page_contents = pypdf.PdfReader(str(target)).pages[0].get_contents()
            assert page_contents is not None
            contents = page_contents.get_data()
            self.assertIn(b"0.917647 0.862745 0.717647 rg", contents)

    def test_replaces_region_and_preserves_page_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "nested" / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.setFont("Helvetica", 12)
            doc.drawString(20, 160, "Original")
            doc.save()

            PDFPatcher(font_size=12).patch(
                source,
                target,
                [PDFReplacement(1, (50, 25, 450, 100), "Translated", (600, 600))],
            )

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 1)
            page = list(reader.pages)[0]
            self.assertIn("Translated", page.extract_text())
            # The original page is preserved; rectangular erasure is visual
            # only and intentionally does not rewrite the source content stream.
            self.assertIn("Original", page.extract_text())
            self.assertEqual(len(list(page.images)), 0)

    def test_rejects_invalid_bbox(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (4, 4, 2, 3), "text", (100, 100)))

    def test_rejects_bbox_outside_page_pixels(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (1, 1, 101, 20), "text", (100, 100)))

    def test_replaces_multi_region_paragraph_with_one_qt_text_layer(self):
        first = PDFReplacementRegion(1, (1, 1, 20, 20), (100, 100), reading_order=1)
        second = PDFReplacementRegion(1, (1, 22, 99, 98), (100, 100), reading_order=2)
        replacement = PDFReplacement(
            1, first.bbox, "translated paragraph", first.page_pixel_size,
            regions=(first, second),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            doc.drawString(1, 90, "Original source page")
            doc.save()

            PDFPatcher(options=PatchTextOptions(max_font_size=8, min_font_size=8)).patch(
                source, target, [replacement]
            )

            page: Any = pypdf.PdfReader(str(target)).pages[0]
            self.assertIn("translated", page.extract_text().replace("\n", "").lower())
            self.assertEqual(len(list(page.images)), 0)

    def test_rejects_single_region_that_disagrees_with_patch_geometry(self):
        region = PDFReplacementRegion(1, (1, 1, 20, 20), (100, 100), reading_order=1)
        cases = (
            PDFReplacement(1, (1, 1, 0, 0), "text", (100, 100), reading_order=1, regions=(region,)),
            PDFReplacement(2, region.bbox, "text", region.page_pixel_size, reading_order=1, regions=(region,)),
        )

        for replacement in cases:
            with self.subTest(replacement=replacement), self.assertRaisesRegex(
                ValueError, "must match its only source region",
            ):
                PDFPatcher().validate(replacement)

    def test_rejects_missing_source_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.save()

            with self.assertRaises(ValueError):
                PDFPatcher().patch(
                    source,
                    root / "target.pdf",
                    [PDFReplacement(2, (1, 1, 10, 10), "text", (100, 100))],
                )

    def test_cjk_multiline_replacement_has_text_layer_and_fits_source_bbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            replacement = PDFReplacement(
                1, (60, 60, 360, 360), "\u8fd9\u662f\u4e00\u6bb5\u6ca1\u6709\u7a7a\u683c\u7684\u4e2d\u6587\u8bd1\u6587\uff0c\u5b83\u5e94\u8be5\u5728\u65b9\u6846\u5185\u81ea\u52a8\u6362\u884c\u3002" * 3, (600, 600)
            )
            patcher = PDFPatcher(options=PatchTextOptions(max_font_size=12, min_font_size=4))
            fitted = QTextParagraphFiller(patcher.options).fit(replacement, {1: (200, 200)})

            patcher.patch(source, target, [replacement])

            resolutions = patcher.font_resolutions
            self.assertEqual(len(resolutions), 1)
            self.assertEqual(resolutions[0].source, "automatic")
            source_page: Any = pypdf.PdfReader(str(source)).pages[0]
            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 1)
            page: Any = reader.pages[0]
            # An absent CJK font is allowed to fall back (even to a missing-glyph
            # font), so Unicode extraction is platform dependent. The English
            # patch tests cover extraction; here prove that CJK layout fits and
            # produces a genuine PDF font/content layer instead of an image.
            for placement in fitted.placements:
                self.assertTrue(all(
                    placement.rectangle.top <= top
                    and top + height <= placement.rectangle.bottom
                    for top, height in zip(placement.line_tops, placement.line_heights)
                ))
            source_fonts = set(source_page["/Resources"]["/Font"].get_object())
            result_fonts = set(page["/Resources"]["/Font"].get_object())
            self.assertGreater(len(result_fonts - source_fonts), 0)
            self.assertGreater(
                len(page.get_contents().get_data()), len(source_page.get_contents().get_data()),
            )
            self.assertEqual(len(list(page.images)), 0)

    def test_preflight_failure_leaves_no_partial_target_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            patcher = PDFPatcher(options=PatchTextOptions(max_font_size=8, min_font_size=8))

            with self.assertRaisesRegex(ValueError, "page 1, bbox"):
                patcher.patch(
                    source,
                    target,
                    [PDFReplacement(1, (10, 10, 30, 30), "too much text " * 100, (200, 200))],
                )
            self.assertFalse(target.exists())

    def test_explicit_skip_records_overflow_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            patcher = PDFPatcher(options=PatchTextOptions(max_font_size=8, min_font_size=8, overflow="skip"))

            patcher.patch(
                source,
                target,
                [PDFReplacement(1, (10, 10, 30, 30), "too much text " * 100, (200, 200))],
            )

            self.assertEqual(len(patcher.skipped_replacements), 1)
            self.assertIn("cannot fit paragraph source regions", patcher.skipped_replacements[0].reason)

    def test_long_cross_page_window_releases_each_source_image_before_the_next(self):
        """Composition consumes serialized page work without materializing a whole window."""
        class TrackingImage:
            def __init__(self, page_index):
                self.page_index = page_index
                self.closed = False

            def close(self):
                self.closed = True

        class TrackingDocument:
            def __init__(self):
                self.images = []
                self.closed = False

            def render_page(self, page_index, _dpi):
                if any(not image.closed for image in self.images):
                    raise AssertionError("previous source image was retained")
                image = TrackingImage(page_index)
                self.images.append(image)
                return image

            def close(self):
                self.closed = True

        class TrackingHandler:
            def __init__(self):
                self.document = TrackingDocument()

            def open(self, _source):
                return self.document

        class LightweightFiller:
            def __init__(self, options):
                self.options = options

            @staticmethod
            def fit(replacement, _page_sizes, _minimum_font_size=None):
                return FittedParagraph(replacement.text, 10, ())

        class RecordingEraser:
            def __init__(self):
                self.image_map_sizes = []

            def plan(self, regions, _page_sizes, page_images):
                self.image_map_sizes.append(len(page_images))
                if len(tuple(regions)) != 1:
                    raise AssertionError("page image must only serve its own regions")
                return ()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            for _ in range(8):
                doc.showPage()
            doc.save()
            handler = TrackingHandler()
            patcher = PDFPatcher(
                options=PatchTextOptions(max_font_size=10, min_font_size=10),
                pdf_handler=cast(PDFHandler, handler),
            )
            patcher._filler = LightweightFiller(patcher.options)  # type: ignore[assignment]
            eraser = RecordingEraser()
            patcher._eraser = eraser  # type: ignore[assignment]
            regions = tuple(
                PDFReplacementRegion(page_index, (1, 1, 99, 99), (100, 100))
                for page_index in range(1, 9)
            )
            replacement = PDFReplacement(
                1, regions[0].bbox, "long paragraph", (100, 100), regions=regions,
            )

            def fail_if_materialized(_plan):
                raise AssertionError("patcher must stream page contributions")

            with patch.object(FillWindowPlan, "paragraphs", new=property(fail_if_materialized)):
                patcher.patch(source, target, [replacement])

            self.assertEqual([image.page_index for image in handler.document.images], list(range(1, 9)))
            self.assertTrue(all(image.closed for image in handler.document.images))
            self.assertEqual(eraser.image_map_sizes, [1] * 8)
            self.assertTrue(handler.document.closed)
