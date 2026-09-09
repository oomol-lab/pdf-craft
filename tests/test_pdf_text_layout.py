import unittest

# pylint: disable=no-member,c-extension-no-member

from pdf_craft.pipeline.pdf import (
    FittedParagraph, PDFReplacement, PDFReplacementRegion, PatchTextOptions, PatchTextStyle,
    QTextParagraphFiller, RegionTextPlacement,
)
from pdf_craft.pipeline.pdf.geometry import PageRectangle
from pdf_craft.pipeline.pdf.text_layout import _choose_automatic_font


def _replacement(text: str, regions, *, layout_ref: str = "text", layout_level: int = 0):
    first = regions[0]
    return PDFReplacement(
        first.page_index, first.bbox, text, first.page_pixel_size,
        regions=tuple(regions), layout_ref=layout_ref, layout_level=layout_level,
    )


class TestQTextParagraphFiller(unittest.TestCase):
    def test_unspecified_font_resolves_to_one_installed_family_for_all_semantic_styles(self):
        """Automatic body and title styles share one real Qt font for a run."""
        from PySide6 import QtGui

        region = PDFReplacementRegion(1, (0, 0, 200, 100), (200, 100))
        filler = QTextParagraphFiller(PatchTextOptions(
            max_font_size=10,
            min_font_size=10,
            styles={"sub_title": PatchTextStyle(max_font_size=10, min_font_size=10)},
        ))

        body = filler.fit(_replacement("中文正文", [region]), {1: (200, 100)})
        title = filler.fit(
            _replacement("中文标题", [region], layout_ref="sub_title", layout_level=1),
            {1: (200, 100)},
        )

        resolutions = filler.font_resolutions
        self.assertEqual(len(resolutions), 1)
        resolution = resolutions[0]
        self.assertEqual(resolution.source, "automatic")
        self.assertIn(resolution.resolved_font_name, QtGui.QFontDatabase.families())
        self.assertEqual(body.placements[0].style.font_name, resolution.resolved_font_name)
        self.assertEqual(title.placements[0].style.font_name, resolution.resolved_font_name)

    def test_automatic_cjk_preference_and_system_fallback_select_real_families(self):
        self.assertEqual(
            _choose_automatic_font(
                ("System Sans", "Noto Sans CJK SC"), "System Sans", True,
            ),
            "Noto Sans CJK SC",
        )
        self.assertEqual(
            _choose_automatic_font(("System Sans",), "System Sans", True),
            "System Sans",
        )

    def test_uses_a_non_quarter_point_style_maximum_with_qt(self):
        """QTextLayout accepts the exact float maximum rather than a 0.25pt grid."""
        region = PDFReplacementRegion(1, (0, 0, 100, 100), (100, 100))
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10.13, min_font_size=4))

        fitted = filler.fit(_replacement("short text", [region]), {1: (100, 100)})

        self.assertEqual(fitted.font_size, 10.13)

    def test_converges_within_fixed_float_tolerance_and_keeps_successful_side(self):
        """The float search must not return a failing candidate at a wrap threshold."""
        threshold = 10.13

        class ThresholdFiller(QTextParagraphFiller):
            def _plan(self, text, replacement, page_sizes, style, font_size):
                del replacement, page_sizes, style
                if font_size > threshold:
                    return None
                return FittedParagraph(text, font_size, ())

        region = PDFReplacementRegion(1, (0, 0, 100, 100), (100, 100))
        fitted = ThresholdFiller(PatchTextOptions(max_font_size=12, min_font_size=4)).fit(
            _replacement("threshold", [region]), {1: (100, 100)},
        )

        self.assertLessEqual(fitted.font_size, threshold)
        self.assertLess(threshold - fitted.font_size, 0.05)
        self.assertNotEqual(fitted.font_size * 4, round(fitted.font_size * 4))

    def test_flows_a_paragraph_through_multiple_rectangles_once(self):
        regions = [
            PDFReplacementRegion(1, (0, 0, 58, 20), (100, 100)),
            PDFReplacementRegion(1, (0, 22, 84, 55), (100, 100)),
            PDFReplacementRegion(1, (0, 57, 84, 100), (100, 100)),
        ]
        text = "one two three four five"
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))

        fitted = filler.fit(_replacement(text, regions), {1: (100, 100)})

        self.assertEqual(fitted.font_size, 10)
        self.assertGreaterEqual(len(fitted.placements), 2)
        self.assertEqual(fitted.placements[0].page_index, 1)
        self.assertTrue(fitted.placements[0].remaining_text.startswith("one two"))
        self.assertNotEqual(fitted.placements[1].remaining_text, fitted.placements[0].remaining_text)

    def test_keeps_ordinary_whitespace_for_qt_to_layout(self):
        """Do not normalize translated whitespace before handing it to Qt."""
        region = PDFReplacementRegion(1, (0, 0, 300, 100), (300, 100))
        text = "alpha  beta\tgamma"
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))

        fitted = filler.fit(_replacement(text, [region]), {1: (300, 100)})

        self.assertEqual(fitted.text, text)
        self.assertEqual(fitted.placements[0].assigned_text, text)

    def test_does_not_split_an_overwide_english_word_character_by_character(self):
        """Qt's default WordWrap must not split ordinary words character by character."""
        regions = [
            PDFReplacementRegion(1, (0, 0, 12, 30), (200, 100)),
            PDFReplacementRegion(1, (0, 32, 180, 80), (200, 100)),
        ]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))

        fitted = filler.fit(_replacement("apple", regions), {1: (200, 100)})

        self.assertEqual(len(fitted.placements), 1)
        self.assertEqual(fitted.placements[0].rectangle, PageRectangle(0, 0, 12, 30))
        self.assertEqual(fitted.placements[0].assigned_text, "apple")

    def test_moves_a_whole_line_to_the_next_rectangle(self):
        regions = [
            PDFReplacementRegion(1, (0, 0, 90, 18), (100, 100)),
            PDFReplacementRegion(1, (0, 20, 90, 80), (100, 100)),
        ]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))

        fitted = filler.fit(_replacement("first line second line third line", regions), {1: (100, 100)})

        self.assertEqual(len(fitted.placements[0].line_tops), 1)
        self.assertGreaterEqual(len(fitted.placements[1].line_tops), 1)
        self.assertTrue(fitted.placements[1].remaining_text.startswith("second"))

    def test_second_pass_keeps_frozen_multi_bbox_text_and_line_ownership(self):
        regions = [
            PDFReplacementRegion(1, (0, 0, 90, 18), (100, 100)),
            PDFReplacementRegion(1, (0, 20, 90, 80), (100, 100)),
        ]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=4))
        fitted = filler.fit(
            _replacement("first line second line third line", regions), {1: (100, 100)},
        )
        self.assertGreaterEqual(len(fitted.placements), 2)

        reflowed = tuple(
            filler.fit_frozen_region(placement, placement.font_size * 0.8)
            for placement in fitted.placements
        )

        self.assertEqual(
            [placement.assigned_text for placement in reflowed],
            [placement.assigned_text for placement in fitted.placements],
        )
        self.assertEqual(
            [len(placement.line_tops) for placement in reflowed],
            [len(placement.line_tops) for placement in fitted.placements],
        )

    def test_second_pass_stops_at_a_deterministic_infeasible_boundary(self):
        class ThresholdFiller(QTextParagraphFiller):
            def _fit_region(
                self, page_index, rectangle, text, style, font_size,
                formula_spans=(), ignore_height=False,
            ):
                del formula_spans, ignore_height
                if font_size > 10:
                    return None, 0
                return RegionTextPlacement(
                    page_index, rectangle, text,
                    (rectangle.x,), (rectangle.width,), (rectangle.top, rectangle.top + 12),
                    (12, 12), font_size, style, assigned_text=text,
                ), len(text)

        style = PatchTextStyle(max_font_size=20, min_font_size=4)
        original = RegionTextPlacement(
            1, PageRectangle(0, 0, 100, 30), "fixed text",
            (0, 0), (50, 50), (0, 12), (12, 12), 8, style,
            assigned_text="fixed text",
        )

        reflowed = ThresholdFiller(PatchTextOptions(styles={"text": style})).fit_frozen_region(
            original, 14,
        )

        self.assertEqual(reflowed.assigned_text, "fixed text")
        self.assertEqual(len(reflowed.line_tops), 2)
        self.assertLessEqual(reflowed.font_size, 10)
        self.assertLess(10 - reflowed.font_size, 0.05)

    def test_selects_largest_uniform_font_size_for_the_paragraph(self):
        regions = [PDFReplacementRegion(1, (0, 0, 100, 0 + 100), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=16, min_font_size=4))

        fitted = filler.fit(_replacement("short text", regions), {1: (100, 100)})

        self.assertEqual(fitted.font_size, 16)

    def test_semantic_style_can_override_default_font_size(self):
        regions = [PDFReplacementRegion(1, (0, 0, 100, 100), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(
            max_font_size=8,
            min_font_size=8,
            styles={"sub_title:2": PatchTextStyle(max_font_size=15, min_font_size=15)},
        ))

        fitted = filler.fit(
            _replacement("A heading", regions, layout_ref="sub_title", layout_level=2),
            {1: (100, 100)},
        )

        self.assertEqual(fitted.font_size, 15)

    def test_missing_or_partial_font_configuration_uses_qt_fallback(self):
        from PySide6 import QtGui

        regions = [PDFReplacementRegion(1, (0, 0, 100, 100), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(
            font_name="font-that-is-not-installed", max_font_size=10, min_font_size=10,
        ))

        fitted = filler.fit(_replacement("中文 mixed text", regions), {1: (100, 100)})

        self.assertEqual(fitted.font_size, 10)
        resolutions = filler.font_resolutions
        self.assertEqual(len(resolutions), 1)
        resolution = resolutions[0]
        self.assertEqual(resolution.source, "qt-fallback")
        self.assertIn(resolution.resolved_font_name, QtGui.QFontDatabase.families())
        self.assertEqual(fitted.placements[0].style.font_name, "font-that-is-not-installed")

    def test_qt_horizontal_alignment_exposes_effective_text_coordinates(self):
        """Qt, not hand-written glyph arithmetic, chooses each line's x offset."""
        region = PDFReplacementRegion(1, (0, 0, 200, 80), (200, 80))
        placements = {}
        for alignment in ("left", "center", "right"):
            style = PatchTextStyle(
                max_font_size=10, min_font_size=10, horizontal_padding=10,
                vertical_padding=5, alignment=alignment,
            )
            fitted = QTextParagraphFiller(PatchTextOptions(styles={"text": style})).fit(
                _replacement("alignment", [region]), {1: (200, 80)},
            )
            placements[alignment] = fitted.placements[0]

        left = placements["left"]
        center = placements["center"]
        right = placements["right"]
        content_right = 190.0
        self.assertAlmostEqual(left.line_text_lefts[0], 10.0)
        self.assertGreater(center.line_text_lefts[0], left.line_text_lefts[0])
        self.assertGreater(right.line_text_lefts[0], center.line_text_lefts[0])
        self.assertAlmostEqual(
            right.line_text_lefts[0] + right.line_text_widths[0], content_right,
        )
        self.assertAlmostEqual(
            center.line_text_lefts[0] - 10.0,
            content_right - (center.line_text_lefts[0] + center.line_text_widths[0]),
            delta=0.1,
        )

    def test_qt_justifies_a_nonfinal_line_to_the_rectangle_width(self):
        region = PDFReplacementRegion(1, (0, 0, 80, 80), (80, 80))
        style = PatchTextStyle(
            max_font_size=10, min_font_size=10, horizontal_padding=5,
            alignment="justify",
        )
        fitted = QTextParagraphFiller(PatchTextOptions(styles={"text": style})).fit(
            _replacement("one two three four five six seven", [region]), {1: (80, 80)},
        )

        placement = fitted.placements[0]
        self.assertGreaterEqual(len(placement.line_tops), 2)
        self.assertAlmostEqual(placement.line_text_lefts[0], 5.0)
        self.assertAlmostEqual(placement.line_text_widths[0], 70.0)

    def test_line_height_and_vertical_alignment_position_complete_lines(self):
        region = PDFReplacementRegion(1, (0, 0, 120, 100), (120, 100))
        vertical_placements = {}
        for alignment in ("top", "center", "bottom"):
            style = PatchTextStyle(
                max_font_size=10, min_font_size=10, vertical_padding=10,
                vertical_alignment=alignment,
            )
            fitted = QTextParagraphFiller(PatchTextOptions(styles={"text": style})).fit(
                _replacement("one line", [region]), {1: (120, 100)},
            )
            vertical_placements[alignment] = fitted.placements[0]

        top = vertical_placements["top"]
        center = vertical_placements["center"]
        bottom = vertical_placements["bottom"]
        self.assertAlmostEqual(top.line_tops[0], 10.0)
        self.assertAlmostEqual(
            center.line_tops[0], 10.0 + (80.0 - center.line_heights[0]) / 2,
        )
        self.assertAlmostEqual(bottom.line_tops[0], 90.0 - bottom.line_heights[0])

        multi_line_style = PatchTextStyle(
            max_font_size=10, min_font_size=10, line_height=1.6,
        )
        multi_line = QTextParagraphFiller(PatchTextOptions(
            styles={"text": multi_line_style}
        )).fit(
            _replacement("one two three", [
                PDFReplacementRegion(1, (0, 0, 55, 100), (55, 100)),
            ]),
            {1: (55, 100)},
        ).placements[0]
        self.assertGreaterEqual(len(multi_line.line_tops), 2)
        self.assertAlmostEqual(
            multi_line.line_tops[1] - multi_line.line_tops[0],
            multi_line.line_heights[0] * 1.6,
        )

    def test_fails_only_when_even_minimum_size_cannot_fit_any_full_line(self):
        regions = [PDFReplacementRegion(1, (0, 0, 20, 5), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=8, min_font_size=8))

        with self.assertRaisesRegex(ValueError, "cannot fit paragraph source regions"):
            filler.fit(_replacement("too much text", regions), {1: (100, 100)})
