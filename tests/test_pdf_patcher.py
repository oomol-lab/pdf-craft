import tempfile
import unittest
from pathlib import Path

import pypdf
from reportlab.pdfgen import canvas

from pdf_craft.pipeline.pdf import PDFPatcher, PDFReplacement


class TestPDFPatcher(unittest.TestCase):
    def test_replaces_region_and_preserves_page_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
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

    def test_rejects_invalid_bbox(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (4, 4, 2, 3), "text", (100, 100)))
