import tempfile
import unittest
from pathlib import Path
from typing import Any

import pypdf
from reportlab.pdfgen import canvas

from pdf_craft.pipeline.pdf import BoxTextLayout, PDFPatcher, PDFReplacement, PatchTextOptions


class TestPDFPatcher(unittest.TestCase):
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
            self.assertNotIn("Original", page.extract_text())

    def test_rejects_invalid_bbox(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (4, 4, 2, 3), "text", (100, 100)))

    def test_rejects_bbox_outside_page_pixels(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (1, 1, 101, 20), "text", (100, 100)))

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

            fitted = BoxTextLayout(patcher.options).fit(replacement.text, 100, 100)
            self.assertGreater(len(fitted.paragraph.blPara.lines), 1)
            self.assertLessEqual(fitted.height + 2, 100)
            patcher.patch(source, target, [replacement])

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 1)
            page: Any = reader.pages[0]
            self.assertIn("\u8fd9\u662f\u4e00\u6bb5", page.extract_text())  # pylint: disable=no-member

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
            self.assertIn("cannot fit bbox", patcher.skipped_replacements[0].reason)
