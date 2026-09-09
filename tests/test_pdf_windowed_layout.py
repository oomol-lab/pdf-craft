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
    def test_second_pass_uses_page_level_weighted_target(self):
        options = PatchTextOptions()
        filler = QTextParagraphFiller(options)
        planner = WindowedParagraphPlanner(filler, {1: (200, 80)}, options)
        short = _replacement("aa", [_region(1)])
        long = _replacement("bbbbbbbb", [_region(1)])
        short_placement = _placement("aa", 8)
        long_placement = _placement("bbbbbbbb", 12)
        statistics = {}
        planner._record_font_size_statistic(statistics, short, short_placement)  # pylint: disable=protected-access
        planner._record_font_size_statistic(statistics, long, long_placement)  # pylint: disable=protected-access
        targets = planner._font_size_targets(statistics)  # pylint: disable=protected-access

        # (2 * 8 + 8 * 12) / 10: the actual run assigned to each bbox is the
        # weight, not the complete paragraph's still-remaining text.
        self.assertEqual(targets, {(1, "text", 0): 11.2})

    def test_second_pass_normalizes_natural_overflow_headlines(self):
        options = PatchTextOptions(styles={
            "sub_title": PatchTextStyle(max_font_size=20, min_font_size=4),
        })

        class Filler(QTextParagraphFiller):
            def __init__(self):
                super().__init__(options)
                self._headline_count = 0

            def fit_headline(self, replacement, page_sizes, minimum_font_size):
                del minimum_font_size
                self._headline_count += 1
                return self._plan_headline_overflow(  # pylint: disable=protected-access
                    replacement, page_sizes, 8 if self._headline_count == 1 else 12,
                )

        filler = Filler()
        planner = WindowedParagraphPlanner(filler, {1: (200, 100)}, options)
        narrow = PDFReplacementRegion(1, (0, 20, 36, 50), (200, 100))
        first = _replacement("A deliberately long heading", [narrow], layout_ref="sub_title")
        second = _replacement("A deliberately long heading", [narrow], layout_ref="sub_title")
        window = next(planner.plan([first, second]))
        try:
            normalized = [item.paragraph.placements[0] for item in window.paragraphs]
            self.assertEqual([placement.font_size for placement in normalized], [10, 10])
            self.assertTrue(all(placement.allows_horizontal_overflow for placement in normalized))
            self.assertTrue(all(len(placement.line_tops) == 1 for placement in normalized))
            self.assertTrue(all(
                placement.line_text_lefts[0] == placement.rectangle.x
                and placement.line_text_widths[0] > placement.rectangle.width
                for placement in normalized
            ))
        finally:
            window.close()

    def test_headline_minimum_uses_normalized_body_placement_size(self):
        class Filler(QTextParagraphFiller):
            def __init__(self):
                super().__init__(PatchTextOptions(headline_min_body_ratio=1.2))
                self.headline_minimum: float | None = None

            def fit(self, replacement, page_sizes, minimum_font_size=None):
                del page_sizes, minimum_font_size
                font_size = 8 if replacement.text == "aa" else 12
                placement = _placement(replacement.text, font_size)
                return FittedParagraph(replacement.text, font_size, (placement,))

            def fit_frozen_region(self, placement, target_font_size, minimum_font_size=None):
                del minimum_font_size
                return replace(placement, font_size=target_font_size)

            def fit_headline(self, replacement, page_sizes, minimum_font_size):
                del page_sizes
                self.headline_minimum = minimum_font_size
                placement = _placement(replacement.text, minimum_font_size)
                return FittedParagraph(replacement.text, minimum_font_size, (placement,))

        filler = Filler()
        planner = WindowedParagraphPlanner(filler, {1: (200, 80)}, filler.options)
        body_short = _replacement("aa", [_region(1)])
        body_long = _replacement("bbbbbbbb", [_region(1)])
        headline = _replacement("heading", [_region(1)], layout_ref="sub_title")

        window = next(planner.plan([body_short, body_long, headline]))
        try:
            # The body target is 11.2pt, so the headline lower bound is based
            # on that normalized on-page result, not the 8pt first pass.
            self.assertIsNotNone(filler.headline_minimum)
            assert filler.headline_minimum is not None
            self.assertAlmostEqual(filler.headline_minimum, 13.44)
            body_sizes = [
                item.paragraph.placements[0].font_size
                for item in window.paragraphs[:2]
            ]
            self.assertEqual(body_sizes, [11.2, 11.2])
        finally:
            window.close()

    def test_second_pass_is_local_and_allows_one_line_to_escape_tight_ocr_height(self):
        region = PDFReplacementRegion(1, (0, 0, 160, 14), (160, 60))
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=20, min_font_size=4))
        fitted = filler.fit(_replacement("one short line", [region]), {1: (160, 60)})
        original = fitted.placements[0]
        self.assertEqual(len(original.line_tops), 1)

        reflowed = filler.fit_frozen_region(original, 14)

        self.assertEqual(reflowed.assigned_text, original.assigned_text)
        self.assertEqual(len(reflowed.line_tops), 1)
        self.assertEqual(reflowed.font_size, 14)
        self.assertGreater(
            reflowed.line_tops[0] + reflowed.line_heights[0], reflowed.rectangle.bottom,
        )

    def test_second_pass_one_line_respects_a_lower_forbidden_line(self):
        """Ignoring a tight OCR bbox must not allow overlap with a footnote."""
        style = PatchTextStyle(max_font_size=12, min_font_size=4)
        original = RegionTextPlacement(
            1, PageRectangle(0, 0, 100, 10), "one short line",
            (0,), (50,), (0,), (8,), 8, style,
            assigned_text="one short line", forbidden_bottom=14,
        )
        filler = QTextParagraphFiller(PatchTextOptions(styles={"text": style}))

        reflowed = filler.fit_frozen_region(original, 12)

        self.assertEqual(reflowed.assigned_text, original.assigned_text)
        self.assertEqual(len(reflowed.line_tops), 1)
        self.assertLess(reflowed.font_size, 12)
        self.assertLessEqual(
            reflowed.line_tops[-1] + reflowed.line_heights[-1], 14 + 1e-6,
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
        self.assertGreater(body_plan.font_size, 12)
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
        # A figure below the first source box is a real forbidden line.  The
        # page below it remains available through the paragraph's second box.
        blocker = PDFReplacementRegion(1, (0, 34, 240, 96), (240, 100))
        body = replace(body, obstacle_regions=(blocker,))
        headline = replace(headline, obstacle_regions=(blocker,))

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

    def test_narrow_headline_respects_its_explicit_ceiling_and_flows_right(self):
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
        self.assertEqual(headline_plan.font_size, 11)
        self.assertTrue(placement.allows_horizontal_overflow)
        self.assertEqual(len(placement.line_tops), 1)
        self.assertAlmostEqual(placement.line_text_lefts[0], placement.rectangle.x)
        self.assertGreater(placement.line_text_widths[0], placement.rectangle.width)
        self.assertAlmostEqual(
            placement.line_tops[0] + placement.line_heights[0] / 2,
            placement.rectangle.top + placement.rectangle.height / 2,
        )
        window.close()

    def test_second_pass_never_exceeds_an_explicit_headline_ceiling(self):
        """Page-local normalization must preserve title max_font_size too."""
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
        narrow = PDFReplacementRegion(1, (0, 20, 36, 50), (200, 100))
        window = next(planner.plan([
            _replacement("Body", [_region(1)]),
            _replacement("A deliberately long heading", [narrow], layout_ref="sub_title"),
            _replacement("Another deliberately long heading", [narrow], layout_ref="sub_title"),
        ]))
        try:
            body, first_headline, second_headline = window.paragraphs
            self.assertEqual(body.paragraph.font_size, 10)
            self.assertEqual(
                [first_headline.paragraph.font_size, second_headline.paragraph.font_size],
                [11, 11],
            )
            self.assertEqual(
                [first_headline.paragraph.placements[0].font_size,
                 second_headline.paragraph.placements[0].font_size],
                [11, 11],
            )
        finally:
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

    def test_second_pass_rewrites_long_window_from_serialized_pages(self):
        """A long window normalizes per page without retaining all placements."""
        page_count = 24
        regions = [
            PDFReplacementRegion(page_index, (0, 0, 240, 32), (240, 100))
            for page_index in range(1, page_count + 1)
        ]
        first = _replacement(
            "one two three four five six seven " * 30, regions,
        )
        second = _replacement(
            "eight nine ten eleven twelve thirteen " * 30, regions,
        )
        options = PatchTextOptions(max_font_size=8, min_font_size=8)
        planner = WindowedParagraphPlanner(
            QTextParagraphFiller(options),
            {page_index: (240, 100) for page_index in range(1, page_count + 1)},
            options,
        )

        window = next(planner.plan([first, second]))
        temporary_root = Path(window._temporary_directory.name)  # pylint: disable=protected-access
        try:
            self.assertEqual(len(window._summaries), 2)  # pylint: disable=protected-access
            self.assertTrue(all(
                not hasattr(summary, "placements")
                for summary in window._summaries  # pylint: disable=protected-access
            ))
            self.assertTrue(temporary_root.is_dir())

            active_pages = []
            for page_index in range(1, page_count + 1):
                contributions = tuple(window.page_contributions(page_index))
                self.assertEqual(len(contributions), 2)
                self.assertTrue(all(
                    contribution.regions == (regions[page_index - 1],)
                    for contribution in contributions
                ))
                if any(contribution.placements for contribution in contributions):
                    active_pages.append(page_index)
                    self.assertTrue(all(
                        placement.page_index == page_index
                        for contribution in contributions
                        for placement in contribution.placements
                    ))
            self.assertGreater(len(active_pages), 1)
        finally:
            window.close()
        self.assertFalse(temporary_root.exists())
