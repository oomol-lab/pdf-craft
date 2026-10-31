"""OCR column boundaries must not masquerade as author paragraph boundaries."""

import unittest

from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.extractor.chapter.reading_serials import split_reading_serials
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.pdf import Page, PageLayout


def _layout(order, bbox, text, ref="text"):
    return PageLayout(ref=ref, det=bbox, text=text, order=order, hash=None)


class VerticalParagraphTests(unittest.TestCase):
    def test_touching_vertical_columns_reach_continuation_check(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "天地玄黃宇宙洪"),
            _layout(1, (250, 50, 300, 850), "荒日月盈昃辰宿列"),
            _layout(2, (200, 50, 250, 850), "張寒來暑往秋收冬藏。"),
        ]
        paragraphs = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(paragraphs), 1)
        paragraph = paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(
            [x.source_order for x in paragraph.children
             if isinstance(x, SourceTextFragment)], [0, 1, 2]
        )
        self.assertTrue(all(isinstance(x, SourceTextFragment)
                            for x in paragraph.children))
        self.assertEqual([x.det for x in layouts],
                         [(300, 50, 350, 850), (250, 50, 300, 850),
                          (200, 50, 250, 850)])

    def test_vertical_numbered_entries_remain_separate(self):
        for prefix1, prefix2 in [("（一）", "（二）"), ("〔九〕", "〔一〇〕"),
                                  ("「二七」", "「二八」")]:
            with self.subTest(prefix=prefix2):
                layouts = [
                    _layout(0, (300, 50, 350, 850), prefix1 + "第一條說明仍待補充，"),
                    _layout(1, (250, 50, 300, 850), prefix2 + "第二條說明獨立成立。"),
                ]
                paragraphs = list(Jointer([(1, layouts)]).execute())
                self.assertEqual(len(paragraphs), 2)

    def test_quoted_number_after_numbered_entry_continuation_starts_entry(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "〔二六〕這條說明還需要補"),
            _layout(1, (250, 50, 300, 850), "充幾個字才能表達清楚，"),
            _layout(2, (200, 50, 250, 850), "「二七」另一條說明另起。"),
        ]
        paragraphs = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(paragraphs), 2)
        paragraph = paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(len(paragraph.children), 2)

    def test_quoted_number_in_ordinary_prose_is_not_an_entry(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "這個計數方法需要我們從"),
            _layout(1, (250, 50, 300, 850), "「一」開始向上數到一百。"),
        ]
        self.assertEqual(len(list(Jointer([(1, layouts)]).execute())), 1)

    def test_vertical_heading_is_not_merged_into_body(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "第一卷天地玄黃", "title"),
            _layout(1, (250, 50, 300, 850), "宇宙洪荒日月盈昃。"),
        ]
        paragraphs = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(paragraphs), 2)
        heading = paragraphs[0]
        assert isinstance(heading, TextFlowItem)
        self.assertEqual(heading.role, "heading")

    def test_dense_horizontal_columns_are_not_vertical_lines(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "這是橫排窄欄中的完整段落。" * 30),
            _layout(1, (250, 50, 300, 850), "這是另一欄中的完整獨立段落。" * 30),
        ]
        self.assertEqual(list(split_reading_serials(layouts)), [layouts])

    def test_stacked_narrow_blocks_are_not_parallel_vertical_columns(self):
        layouts = [
            _layout(0, (300, 50, 350, 400), "獨立的短句段落"),
            _layout(1, (300, 450, 350, 850), "另一個獨立段落"),
        ]
        self.assertEqual(list(split_reading_serials(layouts)), [layouts])

    def test_horizontal_paragraphs_keep_original_grouping(self):
        layouts = [
            _layout(0, (50, 50, 850, 150), "第一個橫排段落尚未完結，"),
            _layout(1, (50, 200, 850, 300), "第二個橫排段落另起。"),
        ]
        paragraphs = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(paragraphs), 2)

    def test_primary_projection_shares_direction_detection_in_both_modes(self):
        layouts = [
            _layout(0, (300, 50, 350, 850), "天地玄黃宇宙洪"),
            _layout(1, (250, 50, 300, 850), "荒日月盈昃辰宿列張。"),
        ]
        page = Page(index=1, image=None, body_layouts=layouts,
                    footnotes_layouts=[], input_tokens=0, output_tokens=0)
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                target = project_window([None, page, None], footnotes)[1]
                assert target is not None
                self.assertFalse(target.layouts[0].continues_from_previous)
                self.assertTrue(target.layouts[1].continues_from_previous)
                self.assertEqual([x.source_order for x in target.layouts], [0, 1])
        self.assertIs(page.body_layouts, layouts)
        self.assertEqual(page.body_layouts[0].text, "天地玄黃宇宙洪")


if __name__ == "__main__":
    unittest.main()
