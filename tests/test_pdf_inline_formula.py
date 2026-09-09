"""Regression coverage for safe inline-formula handling in PDF patches."""

from io import BytesIO
import unittest
from typing import Any, cast

import pypdf

from pdf_craft.formula import latex_to_plain_text
from pdf_craft.pipeline.pdf import PDFInlineFormula, PDFReplacement, PDFReplacementRegion
from pdf_craft.pipeline.pdf.text_layout import (
    PatchTextOptions, QTextParagraphFiller, _ensure_qt_application,
    _qt_modules, _utf16_index_for_python, _x_coordinate,
)
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
        page = cast(Any, pypdf.PdfReader(BytesIO(fragment.pdf)).pages[0])
        self.assertAlmostEqual(float(page.mediabox.width), fragment.width, places=3)  # pylint: disable=no-member
        self.assertAlmostEqual(float(page.mediabox.height), fragment.height, places=3)  # pylint: disable=no-member

    def test_real_tex_renderer_supports_mathbb_when_available(self):
        renderer = InlineFormulaPDFRenderer()
        if not renderer.available:
            self.skipTest("requires a complete local Matplotlib/TeX PDF backend")
        fragment = renderer.render(r"(\mathbb{Z} / q\mathbb{Z})^{*}", 10)
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

    def test_second_pass_rerenders_formula_atom_at_its_local_font_size(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                return FormulaFragment(
                    f"%PDF-{latex}-{point_size}".encode(), point_size * 2, point_size, 2,
                )

        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        replacement = PDFReplacement(
            1, region.bbox, "before \ufffc after", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula("x^2"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=20, min_font_size=4))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        initial = filler.fit(replacement, {1: (200, 100)}).placements[0]

        reflowed = filler.fit_frozen_region(initial, 12)

        # The proxy uses the active platform font's space metrics, so a line
        # boundary can make 12pt infeasible on one platform but not another.
        # The invariant is that the surviving local candidate re-renders the
        # atom at its own final point size rather than retaining the old PDF.
        self.assertLessEqual(reflowed.font_size, initial.font_size)
        self.assertEqual(len(reflowed.formula_draws), 1)
        self.assertEqual(reflowed.formula_draws[0].latex, "x^2")
        self.assertEqual(
            reflowed.formula_draws[0].pdf,
            f"%PDF-x^2-{reflowed.font_size}".encode(),
        )

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

        self.assertEqual(len(fitted.placements[0].formula_draws), 1)

    def test_formula_trailing_space_stays_with_the_atom_across_regions(self):
        """A vector formula must not strand following text at a region edge.

        This is the minimal cross-bbox counterpart of the translated PDF
        regression: a formula is followed by ordinary source whitespace and
        then Chinese punctuation.  The test does not teach Qt a punctuation
        rule; it verifies that the formula's own separator is represented as
        part of that indivisible inline atom.
        """
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 28, 10, 2)

        raw_text = "甲乙\ufffc ，后续文字"
        broad_region = PDFReplacementRegion(1, (0, 0, 280, 80), (300, 100))
        replacement = PDFReplacement(
            1, broad_region.bbox, raw_text, broad_region.page_pixel_size,
            regions=(broad_region,), inline_formulas=(PDFInlineFormula("q"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        style = filler._resolve_font(  # pylint: disable=protected-access
            filler.options.style_for("text", 0), raw_text,
        )
        layout_text, spans = filler._formula_layout_text(  # pylint: disable=protected-access
            raw_text, replacement, style, 10, 278, 78,
        )
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(layout_text[span.start + span.length], "\u00a0")
        self.assertEqual(replacement.text, raw_text)

        # Find a real Qt width where the old proxy would leave the comma at a
        # new-line start but the atom-bound separator makes Qt move the whole
        # formula.  The search avoids hard-coding platform font metrics.
        unbound = (
            layout_text[:span.start + span.length]
            + " "
            + layout_text[span.start + span.length + 1:]
        )
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)

        def lines_for(text, width):
            layout = filler._create_layout(  # pylint: disable=protected-access
                QtCore, QtGui, text, style, 10,
            )
            lines = []
            layout.beginLayout()
            try:
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(width)
                    start = line.textStart()
                    lines.append(text[start:start + line.textLength()])
            finally:
                layout.endLayout()
            return lines

        width = None
        for candidate in range(20, 160):
            unbound_lines = lines_for(unbound, candidate)
            bound_lines = lines_for(layout_text, candidate)
            if (
                len(unbound_lines) >= 2
                and len(bound_lines) >= 2
                and unbound_lines[1].startswith("，")
                and not bound_lines[1].startswith("，")
                and "\u00a0" * span.length in bound_lines[1]
            ):
                width = candidate
                break
        self.assertIsNotNone(width)
        assert width is not None
        first = PDFReplacementRegion(1, (0, 0, width, 22), (300, 100))
        second = PDFReplacementRegion(1, (0, 25, 280, 90), (300, 100))
        flowed = PDFReplacement(
            1, first.bbox, raw_text, first.page_pixel_size,
            regions=(first, second), inline_formulas=(PDFInlineFormula("q"),),
        )

        fitted = filler.fit(flowed, {1: (300, 100)})

        self.assertEqual(len(fitted.placements), 2)
        self.assertEqual(fitted.placements[0].formula_draws, ())
        self.assertEqual(len(fitted.placements[1].formula_draws), 1)
        self.assertFalse(fitted.placements[1].assigned_text.startswith("，"))

    def test_formula_draw_uses_the_wrapped_line_cursor_position(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 10, 10, 2)

        region = PDFReplacementRegion(1, (0, 0, 80, 100), (80, 100))
        replacement = PDFReplacement(
            1, region.bbox, "abcdefghij abc \ufffc", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula("x"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (80, 100)})

        placement = fitted.placements[0]
        self.assertGreaterEqual(len(placement.line_tops), 2)
        self.assertEqual(len(placement.formula_draws), 1)
        # The formula follows abc on line two; a line-relative cursor lookup
        # would incorrectly reset it to the left edge.
        self.assertGreater(placement.formula_draws[0].x, 20)

    def test_formula_draw_uses_utf16_cursor_position_after_astral_character(self):
        class Renderer:
            available = True

            @staticmethod
            def render(latex, point_size):
                del latex, point_size
                return FormulaFragment(b"%PDF-1.4", 10, 10, 2)

        region = PDFReplacementRegion(1, (0, 0, 80, 100), (80, 100))
        replacement = PDFReplacement(
            1, region.bbox, "😀abcdefghij abc \ufffc", region.page_pixel_size,
            regions=(region,), inline_formulas=(PDFInlineFormula("x"),),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        filler._formula_renderer = cast(Any, Renderer())  # pylint: disable=protected-access
        fitted = filler.fit(replacement, {1: (80, 100)})

        placement = fitted.placements[0]
        self.assertGreaterEqual(len(placement.line_tops), 2)
        self.assertEqual(len(placement.formula_draws), 1)
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        layout = filler._create_layout(  # pylint: disable=protected-access
            QtCore, QtGui, placement.remaining_text, placement.style, placement.font_size, 8,
        )
        marker_index = placement.remaining_text.index("\u00a0")
        marker_utf16_index = _utf16_index_for_python(placement.remaining_text, marker_index)
        expected_x = None
        layout.beginLayout()
        try:
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(
                    (placement.rectangle.width - 2 * placement.style.horizontal_padding) * 8,
                )
                if line.textStart() <= marker_utf16_index <= line.textStart() + line.textLength():
                    expected_x = (
                        placement.rectangle.x + placement.style.horizontal_padding
                        + _x_coordinate(line.cursorToX(marker_utf16_index)) / 8
                    )
                    break
        finally:
            layout.endLayout()
        self.assertIsNotNone(expected_x)
        assert expected_x is not None
        self.assertAlmostEqual(placement.formula_draws[0].x, expected_x)

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
