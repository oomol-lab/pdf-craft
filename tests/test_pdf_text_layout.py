import unittest

from pdf_craft.pipeline.pdf import BoxTextLayout, PatchTextOptions


class TestBoxTextLayout(unittest.TestCase):
    def test_wraps_cjk_without_spaces_into_multiple_lines(self):
        layout = BoxTextLayout(PatchTextOptions(max_font_size=12, min_font_size=4))
        fitted = layout.fit("这是没有空格的中文文本，需要在固定宽度的边框中自动换行。" * 3, 80, 100)

        self.assertGreater(len(fitted.paragraph.blPara.lines), 1)
        self.assertLessEqual(fitted.width, 78)
        self.assertLessEqual(fitted.height, 98)

    def test_normalizes_paragraph_whitespace_but_preserves_english_words(self):
        layout = BoxTextLayout(PatchTextOptions(max_font_size=12, min_font_size=4))
        fitted = layout.fit("First paragraph.\n\nSecond   paragraph has words.", 120, 100)

        self.assertIn("First paragraph. Second paragraph has words.", fitted.paragraph.text)

    def test_wraps_mixed_cjk_latin_and_numbers(self):
        layout = BoxTextLayout(PatchTextOptions(max_font_size=12, min_font_size=4))
        fitted = layout.fit("PDFCraft 2.0 \u6df7\u6392 text with 12345 \u548c\u5e38\u89c1\u6807\u70b9\uff0c\u5fc5\u987b\u5b8c\u6574\u653e\u5165\u8fb9\u6846\u3002" * 2, 100, 100)

        self.assertGreater(len(fitted.paragraph.blPara.lines), 1)

    def test_selects_largest_available_font_size(self):
        options = PatchTextOptions(max_font_size=16, min_font_size=4)
        fitted = BoxTextLayout(options).fit("short text", 300, 100)

        self.assertEqual(fitted.font_size, 16)

    def test_fails_when_minimum_font_cannot_fit(self):
        options = PatchTextOptions(max_font_size=8, min_font_size=8)
        with self.assertRaisesRegex(ValueError, "cannot fit bbox"):
            BoxTextLayout(options).fit("too much text " * 100, 20, 10)

    def test_default_cjk_font_is_registered_and_latin_font_rejects_cjk(self):
        self.assertEqual(BoxTextLayout().fit("\u4e2d\u6587", 100, 100).font_size, 12)
        with self.assertRaisesRegex(ValueError, "cannot reliably draw"):
            BoxTextLayout(PatchTextOptions(font_name="Helvetica")).fit("\u4e2d\u6587", 100, 100)

    def test_rejects_unavailable_font(self):
        with self.assertRaisesRegex(ValueError, "font is unavailable"):
            BoxTextLayout(PatchTextOptions(font_name="not-a-font")).fit("text", 100, 100)
