import unittest
from dataclasses import replace

# pylint: disable=no-member,c-extension-no-member

from pdf_craft.pipeline.pdf import (
    FittedParagraph, PDFReplacement, PDFReplacementRegion, PatchTextOptions, PatchTextStyle,
    QTextParagraphFiller, RegionTextPlacement,
)
from pdf_craft.pipeline.pdf.geometry import PageRectangle
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.extractor.chapter.chapter import (
    AssetLayout, BlockLayout, Chapter, ParagraphLayout, Reference,
)
from pdf_craft.pipeline.pdf.text_layout import (
    _LAYOUT_SCALE, _RegionSlotDecision, _choose_automatic_font, _closest_to_aim,
    _qt_modules, _signed_slot_plan_frontier,
)


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

    def test_default_style_fills_a_spacious_single_bbox_beyond_legacy_12pt(self):
        """An unspecified ceiling is automatic rather than a hidden 12pt cap."""
        region = PDFReplacementRegion(1, (0, 0, 1_000, 500), (1_000, 500))
        filler = QTextParagraphFiller(PatchTextOptions())

        fitted = filler.fit(
            _replacement("This translated paragraph has ample room to grow.", [region]),
            {1: (1_000, 500)},
        )

        self.assertGreater(fitted.font_size, 12)
        placement = fitted.placements[0]
        self.assertTrue(all(
            placement.rectangle.top <= top
            and top + height <= placement.rectangle.bottom
            for top, height in zip(placement.line_tops, placement.line_heights)
        ))

    def test_explicit_style_maximum_remains_a_hard_ceiling(self):
        region = PDFReplacementRegion(1, (0, 0, 1_000, 500), (1_000, 500))
        filler = QTextParagraphFiller(PatchTextOptions(
            styles={"text": PatchTextStyle(max_font_size=12, min_font_size=4)},
        ))

        fitted = filler.fit(
            _replacement("This translated paragraph has ample room to grow.", [region]),
            {1: (1_000, 500)},
        )

        self.assertEqual(fitted.font_size, 12)

    def test_global_maximum_is_a_hard_ceiling_for_an_implicit_headline(self):
        region = PDFReplacementRegion(1, (0, 0, 1_000, 500), (1_000, 500))
        options = PatchTextOptions(max_font_size=12, min_font_size=4)
        filler = QTextParagraphFiller(options)

        fitted = filler.fit(
            _replacement(
                "A spacious headline must still respect the configured ceiling.",
                [region], layout_ref="sub_title",
            ),
            {1: (1_000, 500)},
        )

        self.assertEqual(options.style_for("sub_title", 0).max_font_size, 12)
        self.assertEqual(fitted.font_size, 12)

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

    def test_aim_line_can_be_crossed_when_that_is_closer_than_staying_above_it(self):
        """A source bottom is scored as a target, never treated as a hard edge."""
        class AimFiller(QTextParagraphFiller):
            def _plan(self, text, replacement, page_sizes, style, font_size):
                del replacement, page_sizes
                if font_size > 7:
                    return None
                bottom = 8 if font_size <= 5 else 10.5
                placement = RegionTextPlacement(
                    1, PageRectangle(0, 0, 100, 10), text,
                    (0,), (20,), (bottom - 2,), (2,), font_size, style,
                    assigned_text=text, forbidden_bottom=12,
                )
                return FittedParagraph(text, font_size, (placement,))

        region = PDFReplacementRegion(1, (0, 0, 100, 10), (100, 100))
        fitted = AimFiller(PatchTextOptions(max_font_size=10, min_font_size=4)).fit(
            _replacement("target", [region]), {1: (100, 100)},
        )

        placement = fitted.placements[0]
        self.assertGreater(placement.line_tops[-1] + placement.line_heights[-1], 10)
        self.assertLessEqual(placement.line_tops[-1] + placement.line_heights[-1], 12)

    def test_aim_selection_prioritizes_the_actual_terminal_bbox(self):
        """An exact terminal aim wins before a candidate's extra source box."""
        style = PatchTextStyle(max_font_size=12, min_font_size=4)

        def placement(bottom: float) -> RegionTextPlacement:
            return RegionTextPlacement(
                1, PageRectangle(0, 0, 100, 10), "line",
                (0,), (20,), (bottom - 2,), (2,), 8, style, assigned_text="line",
            )

        extra_bbox = FittedParagraph("line", 8, (placement(2), placement(18)))
        exact_terminal_bbox = FittedParagraph("line", 8, (placement(10),))

        self.assertIs(_closest_to_aim((extra_bbox, exact_terminal_bbox)), exact_terminal_bbox)

    def test_multi_bbox_aim_selection_does_not_sacrifice_an_earlier_bbox(self):
        """A perfect final bbox cannot justify a huge earlier target-line miss."""
        style = PatchTextStyle(max_font_size=12, min_font_size=4)

        def placement(bottom: float, aim: float = 10) -> RegionTextPlacement:
            return RegionTextPlacement(
                1, PageRectangle(0, 0, 100, aim), "line",
                (0,), (20,), (bottom - 2,), (2,), 8, style, assigned_text="line",
            )

        terminal_only = FittedParagraph(
            "line", 8, (placement(90), placement(10)),
        )
        balanced = FittedParagraph(
            "line", 8, (placement(12), placement(13)),
        )

        self.assertIs(_closest_to_aim((terminal_only, balanced)), balanced)

    def test_multi_bbox_aim_search_covers_qt_wrap_discontinuities(self):
        """A changed Qt text flow may expose a better aim between end points."""
        regions = [
            PDFReplacementRegion(1, (0, 0, 70, 10), (220, 260)),
            PDFReplacementRegion(1, (0, 20, 70, 40), (220, 260)),
        ]
        fitted = QTextParagraphFiller(PatchTextOptions(min_font_size=4, max_font_size=18)).fit(
            _replacement("one two three four five six seven eight", regions),
            {1: (220, 260)},
        )
        minimum = QTextParagraphFiller(PatchTextOptions(min_font_size=4, max_font_size=4)).fit(
            _replacement("one two three four five six seven eight", regions),
            {1: (220, 260)},
        )

        terminal = fitted.placements[-1]
        terminal_bottom = terminal.line_tops[-1] + terminal.line_heights[-1]
        minimum_terminal = minimum.placements[-1]
        minimum_delta = abs(
            minimum_terminal.line_tops[-1] + minimum_terminal.line_heights[-1]
            - minimum_terminal.rectangle.bottom
        )
        self.assertEqual(len(fitted.placements), 2)
        self.assertGreater(fitted.font_size, 4 + 1e-6)
        self.assertLess(abs(terminal_bottom - terminal.rectangle.bottom), minimum_delta)
        self.assertLessEqual(terminal_bottom, terminal.forbidden_bottom or 260)

    def test_lower_asset_top_is_a_forbidden_line(self):
        source = PDFReplacementRegion(1, (0, 0, 100, 10), (100, 100))
        figure = PDFReplacementRegion(1, (0, 14, 100, 30), (100, 100))
        replacement = PDFReplacement(
            1, source.bbox, "line", source.page_pixel_size,
            regions=(source,), obstacle_regions=(figure,),
        )

        placement = QTextParagraphFiller(PatchTextOptions(
            max_font_size=4, min_font_size=4, vertical_alignment="bottom",
        )).fit(
            replacement, {1: (100, 100)},
        ).placements[0]

        line_bottom = placement.line_tops[-1] + placement.line_heights[-1]
        self.assertEqual(placement.forbidden_bottom, 14)
        self.assertGreater(line_bottom, 10)
        self.assertLessEqual(line_bottom, 14)

    def test_pdf_pipeline_attaches_asset_geometry_as_text_flow_obstacles(self):
        chapter = Chapter(
            id=1,
            level=0,
            layouts=[
                ParagraphLayout("text", 0, [BlockLayout(1, 0, (0, 0, 100, 10), ["body"])]),
                AssetLayout(1, "image", (0, 14, 100, 30), [], [], [], None),
            ],
        )

        replacements = list(PDFTranslationPipeline()._iter_chapter_replacements(  # pylint: disable=protected-access
            chapter, lambda text: text, {1: (100, 100)}, 300, structured=True,
        ))

        self.assertEqual(len(replacements), 1)
        self.assertEqual(replacements[0].obstacle_regions[0].bbox, (0, 14, 100, 30))

    def test_reference_footnote_stops_text_after_its_source_bottom(self):
        """Reference layouts are obstacles even though they are not chapter body layouts."""
        footnote = ParagraphLayout(
            "text", 0, [BlockLayout(1, 1, (0, 14, 100, 30), ["footnote"])],
        )
        reference = Reference(1, 1, "①", [footnote])
        chapter = Chapter(
            id=1,
            level=0,
            layouts=[
                ParagraphLayout(
                    "text", 0,
                    [BlockLayout(1, 0, (0, 0, 100, 10), ["body", reference])],
                ),
            ],
        )

        replacement, = PDFTranslationPipeline()._iter_chapter_replacements(  # pylint: disable=protected-access
            chapter, lambda text: text, {1: (100, 100)}, 300, structured=True,
        )
        self.assertEqual([region.bbox for region in replacement.obstacle_regions], [(0, 14, 100, 30)])

        placement = QTextParagraphFiller(PatchTextOptions(
            max_font_size=4, min_font_size=4, vertical_alignment="bottom",
        )).fit(
            replace(replacement, text="line"), {1: (100, 100)},
        ).placements[0]
        line_bottom = placement.line_tops[-1] + placement.line_heights[-1]
        self.assertGreater(line_bottom, 10)
        self.assertEqual(placement.forbidden_bottom, 14)
        self.assertLessEqual(line_bottom, 14)

    def test_high_precision_planning_and_pdf_draw_share_scaled_qt_coordinates(self):
        """Planning and the real PDF overlay use one unrounded Qt coordinate space."""
        import tempfile
        from pathlib import Path

        import pypdf

        class RecordingFiller(QTextParagraphFiller):
            layout_coordinates: list[tuple[float, float]] = []

            def __init__(self):
                super().__init__(PatchTextOptions(max_font_size=4.12, min_font_size=4.12))
                type(self).layout_coordinates = []

            @staticmethod
            def _create_layout(QtCore, QtGui, text, style, font_size, scale=1.0):
                layout = QTextParagraphFiller._create_layout(
                    QtCore, QtGui, text, style, font_size, scale,
                )
                RecordingFiller.layout_coordinates.append((scale, layout.font().pointSizeF()))
                return layout

        filler = RecordingFiller()
        region = PDFReplacementRegion(1, (0, 0, 160, 60), (160, 60))
        fitted = filler.fit(_replacement("precision overlay", [region]), {1: (160, 60)})
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "precision.pdf"
            filler.draw_pdf_overlay(output, (160, 60), fitted.placements)
            output_text = " ".join(pypdf.PdfReader(str(output)).pages[0].extract_text().split())
            self.assertIn("precision overlay", output_text)

        # The final call comes from ``draw_pdf_overlay``; all planning calls
        # and the actual PDF drawing call use the same scaled Qt font metric.
        self.assertGreaterEqual(len(filler.layout_coordinates), 2)
        self.assertTrue(all(
            scale == _LAYOUT_SCALE and point_size == 4.12 * _LAYOUT_SCALE
            for scale, point_size in filler.layout_coordinates
        ))

    def test_headline_overflow_refuses_to_enter_a_lower_obstacle(self):
        source = PDFReplacementRegion(1, (0, 0, 100, 10), (100, 100))
        footnote = PDFReplacementRegion(1, (0, 14, 100, 30), (100, 100))
        replacement = PDFReplacement(
            1, source.bbox, "A long headline", source.page_pixel_size,
            regions=(source,), obstacle_regions=(footnote,), layout_ref="sub_title",
        )
        filler = QTextParagraphFiller(PatchTextOptions(styles={
            "sub_title": PatchTextStyle(max_font_size=20, min_font_size=20),
        }))

        with self.assertRaisesRegex(ValueError, "lower obstacle or page boundary"):
            filler.fit_headline(replacement, {1: (100, 100)}, 20)

    def test_frozen_overflow_headline_keeps_its_forbidden_line(self):
        style = PatchTextStyle(max_font_size=20, min_font_size=4)
        original = RegionTextPlacement(
            1, PageRectangle(0, 0, 100, 10), "A headline",
            (0,), (60,), (1,), (8,), 8, style,
            allows_horizontal_overflow=True, assigned_text="A headline", forbidden_bottom=14,
        )
        reflowed = QTextParagraphFiller(PatchTextOptions(styles={"sub_title": style})).fit_frozen_region(
            original, 20,
        )

        self.assertEqual(reflowed, original)

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

    def test_multi_page_slots_reserve_the_continuation_bbox_before_qt_layout(self):
        """A first-page box cannot consume lines nominally owned by page two.

        This models a paragraph split at a page boundary.  The old continuous
        layout only changed width between boxes, so a page-one box with no
        lower same-page obstacle could eat the continuation text.  Slot
        capacity is now decided before the one QTextLayout stream is consumed.
        """
        regions = [
            PDFReplacementRegion(1, (0, 0, 180, 60), (200, 100)),
            PDFReplacementRegion(2, (0, 0, 180, 60), (200, 100)),
        ]
        text = " ".join(f"word{index}" for index in range(20))

        fitted = QTextParagraphFiller(PatchTextOptions(
            max_font_size=10, min_font_size=10,
        )).fit(_replacement(text, regions), {1: (200, 100), 2: (200, 100)})

        self.assertEqual([placement.page_index for placement in fitted.placements], [1, 2])
        self.assertTrue(fitted.placements[0].assigned_text)
        self.assertTrue(fitted.placements[1].assigned_text)
        self.assertEqual(
            "".join(placement.assigned_text for placement in fitted.placements), text,
        )
        # The physical first box has room for more rows before the page edge,
        # but its slot decision reserves its continuation space instead.
        self.assertLessEqual(len(fitted.placements[0].line_tops), 3)

    def test_tight_slot_choice_is_rejected_when_centering_hits_projected_guards(self):
        """A nominal bottom crossing is unavailable when its centred half crosses a guard."""
        source = PDFReplacementRegion(1, (0, 10, 100, 30), (100, 100))
        above = PDFReplacementRegion(1, (0, 0, 100, 9), (100, 100))
        below = PDFReplacementRegion(1, (0, 31, 100, 60), (100, 100))
        replacement = PDFReplacement(
            1, source.bbox, "one line", source.page_pixel_size,
            regions=(source,), obstacle_regions=(above, below),
        )
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=10, min_font_size=10))
        # ``fit`` takes the optimized one-box branch, so inspect the compact
        # multi-box planner directly: it is the unit responsible for filtering
        # the symmetric guard violation.
        style = filler._resolve_font(  # pylint: disable=protected-access
            filler.options.style_for("text", 0), replacement.text,
        )
        filler._layout_obstacles = {  # pylint: disable=protected-access
            1: tuple(PageRectangle(*item.bbox) for item in (source, above, below)),
        }
        QtCore, QtGui = _qt_modules()
        plans = filler._slot_decision_plans(  # pylint: disable=protected-access
            QtCore, QtGui, replacement, {1: (100, 100)}, style, 10,
        )

        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].decisions[0].choice, "loose")

    def test_slot_frontier_is_bounded_without_materializing_virtual_rows(self):
        """Hundreds of binary decisions remain a bounded set of compact tuples."""
        rectangle = PageRectangle(0, 0, 1, 1)
        choices = tuple(
            (
                _RegionSlotDecision(1, rectangle, 10**12, 0.25, 0.125, 0, 100, "loose"),
                _RegionSlotDecision(1, rectangle, 10**12 + 1, -0.25, -0.125, 0, 100, "tight"),
            )
            for _ in range(256)
        )

        plans = _signed_slot_plan_frontier(choices)

        self.assertLessEqual(len(plans), 2)
        self.assertTrue(all(len(plan.decisions) == 256 for plan in plans))
        self.assertTrue(all(
            isinstance(decision.line_count, int)
            for plan in plans for decision in plan.decisions
        ))

    def test_multi_bbox_flow_keeps_one_qt_character_stream_across_the_boundary(self):
        """The second bbox must not receive a fresh QTextLayout substring."""
        class RecordingFiller(QTextParagraphFiller):
            layout_texts: list[str] = []

            def __init__(self):
                super().__init__(PatchTextOptions(max_font_size=10, min_font_size=10))
                type(self).layout_texts = []

            @staticmethod
            def _create_layout(QtCore, QtGui, text, style, font_size, scale=1.0):
                RecordingFiller.layout_texts.append(text)
                return QTextParagraphFiller._create_layout(
                    QtCore, QtGui, text, style, font_size, scale,
                )

        regions = [
            PDFReplacementRegion(1, (0, 0, 90, 18), (100, 100)),
            PDFReplacementRegion(1, (0, 20, 90, 80), (100, 100)),
        ]
        text = "first line second line third line fourth line"
        filler = RecordingFiller()

        fitted = filler.fit(_replacement(text, regions), {1: (100, 100)})

        self.assertEqual(len(fitted.placements), 2)
        self.assertEqual("".join(item.assigned_text for item in fitted.placements), text)
        # ``fit`` also samples the multi-bbox aim interval.  Crucially every
        # probe receives the complete paragraph, never the second region's
        # remaining substring.
        self.assertTrue(filler.layout_texts)
        # The slot planner also creates an empty metric layout.  Every layout
        # that consumes paragraph content must nevertheless receive the full
        # stream, never a leftover substring for a later bbox.
        self.assertTrue(all(item in {"", text} for item in filler.layout_texts))
        self.assertTrue(any(item == text for item in filler.layout_texts))

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
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=8, min_font_size=8))
        fitted = filler.fit(
            _replacement(
                "first line second line third line fourth line fifth line sixth line seventh line",
                regions,
            ), {1: (100, 100)},
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

    def test_page_bottom_is_the_last_forbidden_line_when_no_lower_bbox_exists(self):
        regions = [PDFReplacementRegion(1, (0, 0, 20, 5), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=8, min_font_size=8))

        fitted = filler.fit(_replacement("too much text", regions), {1: (100, 100)})

        placement = fitted.placements[0]
        self.assertGreater(placement.line_tops[-1] + placement.line_heights[-1], 5)
        self.assertLessEqual(placement.line_tops[-1] + placement.line_heights[-1], 100)
