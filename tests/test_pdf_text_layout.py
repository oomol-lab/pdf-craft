import unittest

from pdf_craft.pipeline.pdf import (
    PDFReplacement, PDFReplacementRegion, PatchTextOptions, PatchTextStyle,
    QTextParagraphFiller,
)


def _replacement(text: str, regions, *, layout_ref: str = "text", layout_level: int = 0):
    first = regions[0]
    return PDFReplacement(
        first.page_index, first.bbox, text, first.page_pixel_size,
        regions=tuple(regions), layout_ref=layout_ref, layout_level=layout_level,
    )


class TestQTextParagraphFiller(unittest.TestCase):
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
        regions = [PDFReplacementRegion(1, (0, 0, 100, 100), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(
            font_name="font-that-is-not-installed", max_font_size=10, min_font_size=10,
        ))

        fitted = filler.fit(_replacement("中文 mixed text", regions), {1: (100, 100)})

        self.assertEqual(fitted.font_size, 10)

    def test_fails_only_when_even_minimum_size_cannot_fit_any_full_line(self):
        regions = [PDFReplacementRegion(1, (0, 0, 20, 5), (100, 100))]
        filler = QTextParagraphFiller(PatchTextOptions(max_font_size=8, min_font_size=8))

        with self.assertRaisesRegex(ValueError, "cannot fit paragraph source regions"):
            filler.fit(_replacement("too much text", regions), {1: (100, 100)})
