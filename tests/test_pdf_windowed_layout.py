import unittest

from pdf_craft.pipeline.pdf import (
    HeadlineConstraintError, PDFReplacement, PDFReplacementRegion,
    PatchTextOptions, PatchTextStyle, QTextParagraphFiller, WindowedParagraphPlanner,
)


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


class TestWindowedParagraphPlanner(unittest.TestCase):
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

    def test_fails_explicitly_when_headline_cannot_meet_body_constraint(self):
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

        with self.assertRaises(HeadlineConstraintError):
            list(planner.plan([
                _replacement("Body", [_region(1)]),
                _replacement("Heading", [_region(1)], layout_ref="sub_title"),
            ]))

    def test_explicit_headline_level_style_remains_a_hard_limit_when_skipped(self):
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

        self.assertEqual([item.replacement for item in window.paragraphs], [body])
        self.assertEqual(planner.skipped[0][0], headline)
        self.assertIsInstance(planner.skipped[0][1], HeadlineConstraintError)

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
