"""Regression coverage for safe inline-formula handling in PDF patches."""

import unittest
from typing import Any, cast

from pdf_craft.formula import latex_to_plain_text
from pdf_craft.pipeline.pdf import PDFInlineFormula, PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.text_layout import PatchTextOptions, QTextParagraphFiller
from pdf_craft.pipeline.pdf.inline_formula import FormulaFragment
from pdf_craft.pipeline.pdf.inline_formula import InlineFormulaPDFRenderer


class TestPDFInlineFormulaFallback(unittest.TestCase):
    def test_real_tex_renderer_emits_a_vector_pdf_when_available(self):
        renderer = InlineFormulaPDFRenderer()
        if not renderer.available:
            self.skipTest("requires a complete local Matplotlib/TeX PDF backend")
        fragment = renderer.render(r"\frac{a}{b}+\alpha_i", 10)
        self.assertIsNotNone(fragment)
        assert fragment is not None
        self.assertTrue(fragment.pdf.startswith(b"%PDF"))
        self.assertGreater(fragment.width, 0)
        self.assertGreater(fragment.height, fragment.descent)

    def test_plain_text_converter_is_shared_with_epub(self):
        self.assertEqual(latex_to_plain_text(r"\mathbb{Z}\to\mathbb{C}"), "ℤ→ℂ")

    def test_formula_marker_becomes_readable_text_not_latex_source(self):
        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "character \ufffc is primitive", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula(r"\chi(n)"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(
            max_font_size=10, min_font_size=10, render_inline_formulas=False,
        ))
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

    def test_unavailable_backend_uses_plain_text_without_invoking_render(self):
        class Renderer:
            available = False

            @staticmethod
            def render(latex, point_size):
                raise AssertionError("unavailable renderer must not be called")

        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "\ufffc", region.page_pixel_size, regions=(region,),
            inline_formulas=(PDFInlineFormula(r"\alpha"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (200, 100)})
        self.assertIn("α", fitted.text)

    def test_available_renderer_keeps_formula_as_one_vector_draw_atom(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 20, 10, 2)

        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "before \ufffc after", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula("x^2"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (200, 100)})

        draws = fitted.placements[0].formula_draws
        self.assertEqual(len(draws), 1)
        self.assertEqual(draws[0].pdf, b"%PDF-1.4")
        self.assertGreater(draws[0].baseline, fitted.placements[0].line_tops[0])

    def test_one_formula_failure_does_not_hide_a_later_formula(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del point_size
                return None if latex == "bad" else FormulaFragment(b"%PDF-1.4", 10, 10, 2)

        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "\ufffc and \ufffc", region.page_pixel_size, regions=(region,),
            inline_formulas=(PDFInlineFormula("bad"), PDFInlineFormula("good")),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (200, 100)})
        self.assertIn("bad", fitted.text)
        self.assertEqual(len(fitted.placements[0].formula_draws), 1)

    def test_renderer_caches_each_formula_and_size(self):
        renderer = InlineFormulaPDFRenderer()
        renderer._available = False  # pylint: disable=protected-access
        self.assertIsNone(renderer.render("x", 10))
        self.assertIsNone(renderer.render("x", 10))
        self.assertEqual(len(renderer._cache), 1)  # pylint: disable=protected-access

    def test_formula_atom_moves_to_a_later_wider_region_without_being_split(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 60, 10, 2)

        first = PDFReplacementRegion(1, (0, 0, 50, 30), (100, 100))
        second = PDFReplacementRegion(1, (0, 35, 100, 70), (100, 100))
        replacement = PDFReplacement(
            1, first.bbox, "\ufffc", first.page_pixel_size, regions=(first, second),
            inline_formulas=(PDFInlineFormula("x"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (100, 100)})

        self.assertEqual(len(fitted.placements), 1)
        self.assertEqual(fitted.placements[0].rectangle.top, 35.0)
        self.assertEqual(fitted.placements[0].rectangle.width, 100.0)
        self.assertEqual(len(fitted.placements[0].formula_draws), 1)

    def test_tall_fragment_falls_back_before_it_can_escape_a_bbox(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 10, 100, 2)

        region = PDFReplacementRegion(1, (0, 0, 100, 20), (100, 100))
        replacement = PDFReplacement(
            1, region.bbox, "\ufffc", region.page_pixel_size, regions=(region,),
            inline_formulas=(PDFInlineFormula("x"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (100, 100)})
        self.assertEqual(fitted.placements[0].formula_draws, ())
        self.assertIn("x", fitted.text)
