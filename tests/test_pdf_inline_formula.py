"""Regression coverage for safe inline-formula handling in PDF patches."""

import unittest

from pdf_craft.formula import latex_to_plain_text
from pdf_craft.pipeline.pdf import PDFInlineFormula, PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.text_layout import PatchTextOptions, QTextParagraphFiller


class TestPDFInlineFormulaFallback(unittest.TestCase):
    def test_plain_text_converter_is_shared_with_epub(self):
        self.assertEqual(latex_to_plain_text(r"\mathbb{Z}\to\mathbb{C}"), "ℤ→ℂ")

    def test_formula_marker_becomes_readable_text_not_latex_source(self):
        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "character \ufffc is primitive", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula(r"\chi(n)"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        fitted = filler.fit(replacement, {1: (200, 100)})

        self.assertIn("χ(n)", fitted.text)
        self.assertNotIn(r"\chi", fitted.text)
        self.assertNotIn("\ufffc", fitted.text)

    def test_marker_count_mismatch_is_rejected_before_drawing(self):
        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "\ufffc", region.page_pixel_size, regions=(region,),
        )
        with self.assertRaisesRegex(ValueError, "markers"):
            QTextParagraphFiller().fit(replacement, {1: (200, 100)})
