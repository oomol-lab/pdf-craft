"""Pixel observations must survive decoding without becoming guessed text."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from PIL import Image, ImageDraw
from doc_page_extractor.structure import build_structured_page
from doc_page_extractor.types import Layout, LayoutKind, OCRPageResult

from pdf_craft import DeepSeekOCRVendorConfig
from pdf_craft.common import AssetHub
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.chapter.reading_serials import _vertical_text
from pdf_craft.pdf.line_geometry import first_line_inset, has_vertical_columns
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.types import Page, PageLayout, encode, decode


def _printed_lines(*, indented=True):
    image = Image.new("RGB", (100, 70), "white")
    draw = ImageDraw.Draw(image)
    for left, top in [(25 if indented else 5, 4), (5, 24), (5, 44)]:
        draw.rectangle((left, top, 95, top + 10), fill="black")
    return image


class PrintedLineGeometryTests(TestCase):
    def test_first_line_is_measured_against_two_later_full_lines(self):
        with _printed_lines() as image:
            inset = first_line_inset(image, (0, 0, 100, 70))
            assert inset is not None
            self.assertGreater(inset, 1)

    def test_aligned_lines_are_not_indented(self):
        with _printed_lines(indented=False) as image:
            self.assertEqual(first_line_inset(image, (0, 0, 100, 70)), 0)

    def test_two_printed_lines_supply_an_edge_but_one_line_cannot(self):
        with Image.new("RGB", (100, 50), "white") as image:
            draw = ImageDraw.Draw(image)
            draw.rectangle((25, 4, 95, 14), fill="black")
            self.assertIsNone(first_line_inset(image, (0, 0, 100, 50)))
            draw.rectangle((5, 24, 95, 34), fill="black")
            inset = first_line_inset(image, (0, 0, 100, 50))
            assert inset is not None
            self.assertGreater(inset, 1)

    def test_inconsistent_later_edges_do_not_establish_a_baseline(self):
        with Image.new("RGB", (120, 100), "white") as image:
            draw = ImageDraw.Draw(image)
            for left, top in [(50, 4), (5, 24), (25, 44), (45, 64)]:
                draw.rectangle((left, top, 119, top + 10), fill="black")
            self.assertIsNone(first_line_inset(image, (0, 0, 120, 100)))

    def test_asset_interfering_with_first_line_is_unknown(self):
        with _printed_lines() as image:
            self.assertIsNone(first_line_inset(image, (0, 0, 100, 70), [(0, 0, 40, 20)]))

    def test_blank_ink_and_tall_vertical_frame_are_unknown(self):
        with Image.new("RGB", (100, 70), "white") as image:
            self.assertIsNone(first_line_inset(image, (0, 0, 100, 70)))
            self.assertIsNone(first_line_inset(image, (0, 0, 20, 70)))

    def test_large_frame_does_not_allocate_crop(self):
        image = Mock(spec=Image.Image)
        image.mode, image.width, image.height = "RGB", 3000, 2000
        self.assertIsNone(first_line_inset(image, (0, 0, 3000, 2000)))
        image.crop.assert_not_called()

    def test_decoded_printed_indent_closes_cross_page_paragraph(self):
        node = PageExtractorNode(DeepSeekOCRVendorConfig("https://unused.invalid", "unused", "unused"))
        text = "接下來是另一個完整段落，這些文字不屬於上一段。"
        layout = Layout((0, 0, 100, 70), text, kind=LayoutKind.TEXT)
        response = OCRPageResult([layout], "test", structured=build_structured_page([layout]))
        with _printed_lines() as image, TemporaryDirectory() as directory:
            page = node.results2page(results=[(image, response)], page_index=2,
                asset_hub=AssetHub(Path(directory)), includes_footnotes=False,
                raw_image=None, plot_path=None, input_tokens=0, output_tokens=0,
                aborted=lambda: False)
        previous = Page(1, None, [PageLayout("text", (0, 0, 100, 70),
                         "他已經說完：“我們回家吧。”", 0, None)], [], 0, 0)
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                current = project_window([previous, page, None], footnotes)[1]
                assert current is not None
                self.assertFalse(current.layouts[0].continues_from_previous)
                self.assertEqual(current.layouts[0].source_order, 0)
                origin = current.layouts[0].source.origin
                assert origin is not None
                self.assertEqual(origin.text, text)

    def test_inline_math_does_not_disable_visible_first_line_observation(self):
        node = PageExtractorNode(DeepSeekOCRVendorConfig("https://unused.invalid", "unused", "unused"))
        text = r"A new paragraph with \(x\) keeps its original mathematical text."
        layout = Layout((0, 0, 100, 70), text, kind=LayoutKind.TEXT)
        response = OCRPageResult([layout], "test", structured=build_structured_page([layout]))
        with _printed_lines() as image, TemporaryDirectory() as directory:
            page = node.results2page(results=[(image, response)], page_index=2,
                asset_hub=AssetHub(Path(directory)), includes_footnotes=False,
                raw_image=None, plot_path=None, input_tokens=0, output_tokens=0,
                aborted=lambda: False)
        observed = page.body_layouts[0]
        self.assertEqual(observed.text, text)
        self.assertIsNotNone(observed.first_line_inset)
        assert observed.first_line_inset is not None
        self.assertGreater(observed.first_line_inset, 1)
        self.assertFalse(observed.vertical_columns)

    def test_optional_observation_roundtrips_and_old_cache_remains_unknown(self):
        for inset in (None, 0.0, 1.25):
            with self.subTest(inset=inset):
                page = Page(1, None, [PageLayout("text", (0, 0, 100, 70),
                            "原始文字", 0, None, first_line_inset=inset)], [], 0, 0)
                restored = decode(encode(page))
                self.assertEqual(restored.body_layouts[0].first_line_inset, inset)
                self.assertEqual(restored.body_layouts[0].text, "原始文字")

    def test_measured_inset_cannot_split_unfinished_word(self):
        previous = Page(1, None, [PageLayout("text", (0, 0, 100, 70),
                         "他慢慢走到了河", 0, None)], [], 0, 0)
        current = Page(2, None, [PageLayout("text", (0, 0, 100, 70),
                        "街的盡頭。", 0, None, first_line_inset=2)], [], 0, 0)
        result = project_window([previous, current, None], False)[1]
        assert result is not None
        self.assertTrue(result.layouts[0].continues_from_previous)

    def test_reference_after_punctuation_is_preserved(self):
        text = "這一段已經說完。①"
        previous = Page(1, None, [PageLayout("text", (0, 0, 100, 70), text, 0, None)], [], 0, 0)
        current = Page(2, None, [PageLayout("text", (0, 0, 100, 70),
                        "另一段完整文字。", 0, None, first_line_inset=2)], [], 0, 0)
        result = project_window([previous, current, None], False)[1]
        assert result is not None
        self.assertFalse(result.layouts[0].continues_from_previous)
        self.assertEqual(previous.body_layouts[0].text, text)


class PrintedColumnGeometryTests(TestCase):
    @staticmethod
    def columns():
        image = Image.new("RGB", (60, 240), "white")
        draw = ImageDraw.Draw(image)
        # Staggered glyph rows produce column gutters; a middle gap ensures
        # the observation does not mistake uninterrupted rules for text.
        for x, offset in [(5, 0), (23, 4), (41, 8)]:
            for y in range(10 + offset, 220, 14):
                if 100 <= y <= 124:
                    continue
                draw.rectangle((x, y, x + 9, y + 9), fill="black")
        return image

    def test_staggered_vertical_glyphs_supply_positive_evidence(self):
        with self.columns() as image:
            self.assertTrue(has_vertical_columns(image, (0, 0, 60, 240)))

    def test_dense_horizontal_rows_and_aligned_grid_remain_unknown(self):
        with Image.new("RGB", (60, 240), "white") as image:
            draw = ImageDraw.Draw(image)
            for y in range(10, 220, 16):
                for x in (5, 23, 41):
                    draw.rectangle((x, y, x + 9, y + 9), fill="black")
            self.assertFalse(has_vertical_columns(image, (0, 0, 60, 240)))

    def test_blank_invalid_and_large_frames_do_not_supply_evidence(self):
        with Image.new("RGB", (60, 240), "white") as image:
            self.assertFalse(has_vertical_columns(image, (0, 0, 60, 240)))
            self.assertFalse(has_vertical_columns(image, (-1, 0, 60, 240)))
        image = Mock(spec=Image.Image)
        image.mode, image.width, image.height = "RGB", 1000, 5000
        self.assertFalse(has_vertical_columns(image, (0, 0, 1000, 5000)))
        image.crop.assert_not_called()

    def test_decoder_and_cache_keep_multicolumn_evidence_for_primary_projection(self):
        node = PageExtractorNode(DeepSeekOCRVendorConfig("https://unused.invalid", "unused", "unused"))
        text = "".join(chr(code) for code in range(0x4e00, 0x4e80))
        layout = Layout((0, 0, 60, 240), text, kind=LayoutKind.TEXT)
        response = OCRPageResult([layout], "test", structured=build_structured_page([layout]))
        with self.columns() as image, TemporaryDirectory() as directory:
            page = node.results2page(results=[(image, response)], page_index=1,
                asset_hub=AssetHub(Path(directory)), includes_footnotes=False,
                raw_image=None, plot_path=None, input_tokens=0, output_tokens=0,
                aborted=lambda: False)
        self.assertTrue(page.body_layouts[0].vertical_columns)
        restored = decode(encode(page))
        self.assertTrue(restored.body_layouts[0].vertical_columns)
        self.assertEqual(restored.body_layouts[0].text, text)
        self.assertTrue(_vertical_text(restored.body_layouts[0]))
        first = restored.body_layouts[0]
        first.det = (60, 0, 120, 240)
        restored.body_layouts.append(PageLayout("text", (0, 0, 60, 240),
            text, 1, None, vertical_columns=True))
        for footnotes in (False, True):
            current = project_window([None, restored, None], footnotes)[1]
            assert current is not None
            self.assertTrue(current.layouts[1].continues_from_previous)
        restored.body_layouts[0].vertical_columns = False
        self.assertFalse(_vertical_text(restored.body_layouts[0]))
        legacy = decode(encode(restored))
        self.assertFalse(legacy.body_layouts[0].vertical_columns)
