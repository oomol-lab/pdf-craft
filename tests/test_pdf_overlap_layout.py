"""Intersecting source boxes must not create fictitious expansion space."""
from dataclasses import replace
from unittest import TestCase

from pdf_craft.pipeline.pdf import (
    PDFReplacement, PDFReplacementRegion, PatchTextOptions, QTextParagraphFiller,
)
from pdf_craft.pipeline.pdf.geometry import PageRectangle
from pdf_craft.pipeline.pdf.text_layout import _vertical_boundaries


class OverlapBoundaryTests(TestCase):
    def test_intersections_protect_the_occupied_side_including_nested_boxes(self):
        rectangle = PageRectangle(20, 100, 200, 60)
        cases = [
            (PageRectangle(30, 158, 180, 40), (0, 160)),
            (PageRectangle(30, 80, 180, 22), (100, 400)),
            (PageRectangle(30, 110, 180, 20), (100, 160)),
            (PageRectangle(10, 90, 220, 100), (100, 160)),
            (PageRectangle(30, 100, 180, 20), (100, 160)),
            (PageRectangle(30, 140, 180, 20), (100, 160)),
            (PageRectangle(30, 80, 180, 20), (100, 400)),
            (PageRectangle(30, 160, 180, 20), (0, 160)),
        ]
        for obstacle, expected in cases:
            with self.subTest(obstacle=obstacle):
                self.assertEqual(_vertical_boundaries(rectangle, [rectangle, obstacle], 400), expected)
                self.assertEqual(_vertical_boundaries(rectangle, [obstacle, rectangle], 400), expected)

    def test_real_clearance_and_other_columns_remain_available(self):
        rectangle = PageRectangle(20, 100, 200, 60)
        obstacles = [
            rectangle, PageRectangle(30, 50, 180, 30),
            PageRectangle(30, 200, 180, 20), PageRectangle(230, 90, 100, 100),
        ]
        self.assertEqual(_vertical_boundaries(rectangle, obstacles, 400), (80, 200))
        self.assertEqual(_vertical_boundaries(rectangle, reversed(obstacles), 400), (80, 200))

    def test_first_pass_does_not_centre_extra_rows_into_an_overlapping_neighbour(self):
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=12, min_font_size=4))
        filler.set_layout_obstacles({1: (PageRectangle(20, 100, 300, 60),
                                         PageRectangle(20, 158, 300, 42))})
        text = '\u2028'.join(['A short translated line.'] * 5)
        fitted = filler.fit(PDFReplacement(1, (20, 100, 320, 160), text, (400, 400)), {1: (400, 400)})
        placement = fitted.placements[0]
        self.assertFalse(placement.force_written)
        self.assertEqual(placement.assigned_text, text)
        self.assertEqual(len(placement.line_tops), 5)
        self.assertLessEqual(placement.line_tops[-1] + placement.line_heights[-1], 160 + 1e-6)

    def test_two_stage_fit_does_not_amplify_a_small_source_overlap(self):
        """Keep all five lines, rather than enlarging into the next paragraph."""
        first = PDFReplacementRegion(1, (20, 100, 320, 160), (400, 400))
        second = PDFReplacementRegion(1, (20, 158, 320, 200), (400, 400))
        text = '\u2028'.join(['A short line of translated text.'] * 5)
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=6, min_font_size=4))
        filler.set_layout_obstacles({1: (PageRectangle(20, 100, 300, 60),
                                         PageRectangle(20, 158, 300, 42))})
        fitted = filler.fit(PDFReplacement(
            1, first.bbox, text, first.page_pixel_size, obstacle_regions=(first, second),
        ), {1: (400, 400)})
        initial = fitted.placements[0]
        self.assertFalse(initial.force_written)
        self.assertEqual(len(initial.line_tops), 5)
        self.assertLessEqual(initial.line_tops[-1] + initial.line_heights[-1], 160 + 1e-6)
        # Model a larger page-level target without constraining it to the
        # deliberately small first-pass ceiling used to fix the reproduction.
        initial = replace(initial, style=replace(initial.style, max_font_size=20))
        final = filler.fit_frozen_region(initial, 12)
        self.assertEqual(final.assigned_text, text)
        self.assertEqual(len(final.line_tops), 5)
        self.assertLessEqual(final.line_tops[-1] + final.line_heights[-1], 160 + 1e-6)
