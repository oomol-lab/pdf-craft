import unittest
from dataclasses import replace
from pathlib import Path

from pdf_craft.pipeline.pdf import (
    PDFReplacement, PDFReplacementRegion,
    FittedParagraph, PatchTextOptions, PatchTextStyle, QTextParagraphFiller,
    RegionTextPlacement, WindowedParagraphPlanner,
)
from pdf_craft.pipeline.pdf.geometry import PageRectangle


def _replacement(
    text: str,
    regions: list[PDFReplacementRegion],
    *,
    layout_ref: str = "text",
    layout_level: int = 0,
) -> PDFReplacement:
    first = regions[0]
    return PDFReplacement(
        first.page_index, first.bbox, text, first.page_pixel_size,
        regions=tuple(regions), layout_ref=layout_ref, layout_level=layout_level,
    )


def _region(page_index: int) -> PDFReplacementRegion:
    return PDFReplacementRegion(page_index, (0, 0, 200, 100), (200, 100))


def _line_region(page_index: int) -> PDFReplacementRegion:
    return PDFReplacementRegion(page_index, (0, 0, 240, 32), (240, 100))


def _placement(text: str, font_size: float, *, lines: int = 1) -> RegionTextPlacement:
    style = PatchTextStyle(max_font_size=24, min_font_size=4)
    return RegionTextPlacement(
        1, PageRectangle(0, 0, 200, 80), text,
        (0.0,) * lines, (20.0,) * lines,
        tuple(float(index * 12) for index in range(lines)), (12.0,) * lines,
        font_size, style, assigned_text=text,
    )


class TestWindowedParagraphPlanner(unittest.TestCase):
    def test_second_pass_uses_page_level_weighted_target_without_moving_text(self):
        options = PatchTextOptions()
        filler = QTextParagraphFiller(options)
        planner = WindowedParagraphPlanner(filler, {1: (200, 80)}, options)
        short = _replacement("aa", [_region(1)])
        long = _replacement("bbbbbbbb", [_region(1)])
        short_placement = _placement("aa", 8)
        long_placement = _placement("bbbbbbbb", 12)
        observed: list[tuple[str, float]] = []

        def record(placement, target, minimum=None):
            del minimum
            observed.append((placement.assigned_text, target))
            return replace(placement, font_size=target)

        filler.fit_frozen_region = record  # type: ignore[method-assign]
        normalized = planner._normalize_window_placements([  # pylint: disable=protected-access
            (short, FittedParagraph(short.text, 8, (short_placement,))),
            (long, FittedParagraph(long.text, 12, (long_placement,))),
        ])

        # (2 * 8 + 8 * 12) / 10: the actual run assigned to each bbox is the
        # weight, not the complete paragraph's still-remaining text.
        self.assertEqual(observed, [("aa", 11.2), ("bbbbbbbb", 11.2)])
        self.assertEqual(normalized[0][1].placements[0].assigned_text, "aa")
        self.assertEqual(normalized[1][1].placements[0].assigned_text, "bbbbbbbb")

    def test_second_pass_is_local_and_allows_one_line_to_escape_tight_ocr_height(self):
        region = PDFReplacementRegion(1, (0, 0, 160, 14), (160, 14))
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=20, min_font_size=4))
        fitted = filler.fit(_replacement("one short line", [region]), {1: (160, 14)})
        original = fitted.placements[0]
        self.assertEqual(len(original.line_tops), 1)

        reflowed = filler.fit_frozen_region(original, 14)

        self.assertEqual(reflowed.assigned_text, original.assigned_text)
        self.assertEqual(len(reflowed.line_tops), 1)
        self.assertEqual(reflowed.font_size, 14)
        self.assertGreater(
            reflowed.line_tops[0] + reflowed.line_heights[0], reflowed.rectangle.bottom,
        )

    def test_second_pass_keeps_multi_line_runs_inside_their_bbox(self):
        region = PDFReplacementRegion(1, (0, 0, 80, 30), (80, 30))
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=20, min_font_size=4))
        fitted = filler.fit(
            _replacement("one two three four five six", [region]), {1: (80, 30)},
        )
        original = fitted.placements[0]
        self.assertGreaterEqual(len(original.line_tops), 2)

        reflowed = filler.fit_frozen_region(original, 20)

        self.assertEqual(reflowed.assigned_text, original.assigned_text)
        self.assertEqual(len(reflowed.line_tops), len(original.line_tops))
        self.assertLessEqual(
            reflowed.line_tops[-1] + reflowed.line_heights[-1],
            reflowed.rectangle.bottom + 1e-6,
        )

    def test_default_style_reserves_headline_font_size_headroom(self):
        options = PatchTextOptions()
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )
        headline = _replacement("Heading", [_region(1)], layout_ref="sub_title")
        body = _replacement("Body", [_region(1)])

        window = next(planner.plan([headline, body]))

        body_plan, headline_plan = (item.paragraph for item in window.paragraphs)
        self.assertEqual(body_plan.font_size, 12)
        self.assertGreaterEqual(headline_plan.font_size, body_plan.font_size * 1.2)

    def test_implicit_headline_uses_the_actual_generic_body_style_ceiling(self):
        options = PatchTextOptions(
            styles={"text": PatchTextStyle(max_font_size=20, min_font_size=20)},
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )
        body = _replacement("Body", [_region(1)])
        headline = _replacement("Heading", [_region(1)], layout_ref="sub_title")

        window = next(planner.plan([body, headline]))
        try:
            body_plan, headline_plan = (item.paragraph for item in window.paragraphs)
            self.assertEqual(body_plan.font_size, 20)
            self.assertGreaterEqual(headline_plan.font_size, 24)
        finally:
            window.close()

    def test_implicit_headline_uses_the_actual_levelled_body_style_ceiling(self):
        options = PatchTextOptions(
            styles={"text:1": PatchTextStyle(max_font_size=20, min_font_size=20)},
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )
        body = _replacement("Body", [_region(1)], layout_level=1)
        headline = _replacement("Heading", [_region(1)], layout_ref="sub_title")

        window = next(planner.plan([body, headline]))
        try:
            body_plan, headline_plan = (item.paragraph for item in window.paragraphs)
            self.assertEqual(body_plan.font_size, 20)
            self.assertGreaterEqual(headline_plan.font_size, 24)
        finally:
            window.close()

    def test_plans_body_before_headline_and_applies_page_body_minimum(self):
        options = PatchTextOptions(
            styles={
                "text": PatchTextStyle(max_font_size=10, min_font_size=10),
                "sub_title": PatchTextStyle(max_font_size=20, min_font_size=4),
            },
            headline_min_body_ratio=1.2,
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )
        headline = _replacement("Heading", [_region(1)], layout_ref="sub_title")
        body = _replacement("Body", [_region(1)])

        window = next(planner.plan([headline, body]))

        self.assertEqual([item.replacement for item in window.paragraphs], [body, headline])
        headline_plan = window.paragraphs[1].paragraph
        self.assertGreaterEqual(headline_plan.font_size, 12)

    def test_cross_page_body_and_headline_use_their_drawn_pages(self):
        options = PatchTextOptions(
            styles={
                "text": PatchTextStyle(max_font_size=10, min_font_size=10),
                "text:1": PatchTextStyle(max_font_size=12, min_font_size=12),
                "sub_title": PatchTextStyle(max_font_size=16, min_font_size=4),
            },
            headline_min_body_ratio=1.3,
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (240, 100), 2: (240, 100)}, options,
        )
        headline = _replacement(
            "one two three four five six seven eight",
            [_line_region(1), _line_region(2)], layout_ref="sub_title",
        )
        body = _replacement(
            "one two three four five six seven eight nine ten",
            [_line_region(1), _line_region(2)],
        )
        larger_second_page_body = _replacement(
            "extra", [_line_region(2)], layout_level=1,
        )

        window = next(planner.plan([headline, body, larger_second_page_body]))

        body_plan = next(
            item.paragraph for item in window.paragraphs if item.replacement is body
        )
        headline_plan = next(
            item.paragraph for item in window.paragraphs if item.replacement is headline
        )
        self.assertEqual({placement.page_index for placement in body_plan.placements}, {1, 2})
        self.assertEqual({placement.page_index for placement in headline_plan.placements}, {1, 2})
        self.assertGreaterEqual(headline_plan.font_size, 15.6)

    def test_headline_without_a_body_uses_configured_deterministic_fallback(self):
        options = PatchTextOptions(
            styles={"sub_title": PatchTextStyle(max_font_size=20, min_font_size=4)},
            headline_fallback_font_size=14,
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )

        window = next(planner.plan([
            _replacement("Heading", [_region(1)], layout_ref="sub_title"),
        ]))

        self.assertGreaterEqual(window.paragraphs[0].paragraph.font_size, 14)

    def test_narrow_headline_keeps_body_relative_minimum_and_flows_right(self):
        options = PatchTextOptions(
            styles={
                "text": PatchTextStyle(max_font_size=10, min_font_size=10),
                "sub_title": PatchTextStyle(max_font_size=11, min_font_size=4),
            },
            headline_min_body_ratio=1.2,
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )

        narrow_title = PDFReplacementRegion(1, (0, 20, 36, 50), (200, 100))
        window = next(planner.plan([
            _replacement("Body", [_region(1)]),
            _replacement(
                "A deliberately long heading", [narrow_title], layout_ref="sub_title",
            ),
        ]))

        _, headline_plan = (item.paragraph for item in window.paragraphs)
        placement = headline_plan.placements[0]
        self.assertEqual(headline_plan.font_size, 12)
        self.assertTrue(placement.allows_horizontal_overflow)
        self.assertEqual(len(placement.line_tops), 1)
        self.assertAlmostEqual(placement.line_text_lefts[0], placement.rectangle.x)
        self.assertGreater(placement.line_text_widths[0], placement.rectangle.width)
        self.assertAlmostEqual(
            placement.line_tops[0] + placement.line_heights[0] / 2,
            placement.rectangle.top + placement.rectangle.height / 2,
        )
        window.close()

    def test_headline_never_uses_generic_skip_overflow_policy(self):
        options = PatchTextOptions(
            styles={
                "text": PatchTextStyle(max_font_size=10, min_font_size=10),
                "sub_title:2": PatchTextStyle(max_font_size=11, min_font_size=4),
            },
            headline_min_body_ratio=1.2,
            overflow="skip",
        )
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100)}, options,
        )
        body = _replacement("Body", [_region(1)])
        headline = _replacement(
            "Heading", [_region(1)], layout_ref="sub_title", layout_level=2,
        )

        window = next(planner.plan([body, headline]))

        self.assertEqual([item.replacement for item in window.paragraphs], [body, headline])
        self.assertTrue(window.paragraphs[1].paragraph.placements[0].allows_horizontal_overflow)
        self.assertEqual(planner.skipped, [])
        window.close()

    def test_emits_a_closed_window_before_consuming_later_pages(self):
        options = PatchTextOptions(max_font_size=10, min_font_size=10)
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options), {1: (200, 100), 2: (200, 100), 3: (200, 100)}, options,
        )
        consumed: list[int] = []

        def source():
            for page_index in (1, 2, 3):
                consumed.append(page_index)
                yield _replacement(f"page {page_index}", [_region(page_index)])

        windows = planner.plan(source())
        first = next(windows)

        self.assertEqual((first.first_page_index, first.last_page_index), (1, 1))
        self.assertEqual(consumed, [1, 2])

    def test_serializes_long_paragraph_placements_by_page(self):
        """A long paragraph keeps summaries in memory, not every glyph placement."""
        page_count = 24
        regions = [
            PDFReplacementRegion(page_index, (0, 0, 240, 32), (240, 100))
            for page_index in range(1, page_count + 1)
        ]
        replacement = _replacement(
            "one two three four five six seven " * 30, regions,
        )
        options = PatchTextOptions(max_font_size=8, min_font_size=8)
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options),
            {page_index: (240, 100) for page_index in range(1, page_count + 1)},
            options,
        )

        window = next(planner.plan([replacement]))
        temporary_root = Path(window._temporary_directory.name)  # pylint: disable=protected-access
        try:
            self.assertEqual(len(window._summaries), 1)  # pylint: disable=protected-access
            self.assertFalse(hasattr(window._summaries[0], "placements"))  # pylint: disable=protected-access
            self.assertTrue(temporary_root.is_dir())

            active_pages = []
            for page_index in range(1, page_count + 1):
                contributions = tuple(window.page_contributions(page_index))
                self.assertEqual(len(contributions), 1)
                self.assertEqual(contributions[0].regions, (regions[page_index - 1],))
                if contributions[0].placements:
                    active_pages.append(page_index)
                    self.assertTrue(all(
                        placement.page_index == page_index
                        for placement in contributions[0].placements
                    ))
            self.assertGreater(len(active_pages), 1)
        finally:
            window.close()
        self.assertFalse(temporary_root.exists())
