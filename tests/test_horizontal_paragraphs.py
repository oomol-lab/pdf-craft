"""Use visible single-line indentation without inventing unseen first lines."""

import unittest

from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.pdf import Page, PageLayout


def _layout(order, bbox, text, ref="text"):
    return PageLayout(ref=ref, det=bbox, text=text, order=order, hash=None)


def _can_join(layouts, *, across_assets=False):
    paragraphs = [TextFlowItem(role="body", level=-1, children=[
        SourceTextFragment(page_index=1, source_order=x.order, bbox=x.det,
                           content=[x.text])]) for x in layouts]
    return Jointer([])._can_merge_paragraphs(  # pylint: disable=protected-access
        paragraphs[0], paragraphs[1], across_assets=across_assets)


class HorizontalParagraphTests(unittest.TestCase):
    def test_column_wrap_within_one_histogram_group_is_joined(self):
        # A page-wide image bridges the gutter in the projection histogram.
        layouts = [
            _layout(0, (217, 257, 2247, 1617), "", "image"),
            _layout(1, (242, 1894, 1198, 2924),
                    "A decorated pavement features an emblem of"),
            _layout(2, (1265, 1894, 2193, 1993),
                    "a mythical hero, framed by a labyrinth pattern."),
            _layout(3, (1265, 2000, 2218, 2980),
                    "Another complete paragraph begins here."),
        ]
        result = list(Jointer([(1, layouts)]).execute())
        paragraphs = [item for item in result if isinstance(item, TextFlowItem)]
        self.assertEqual([[x.source_order for x in item.children
                           if isinstance(x, SourceTextFragment)]
                          for item in paragraphs], [[1, 2], [3]])

    def test_short_column_tail_can_continue_at_the_top_of_next_column(self):
        layouts = [
            _layout(0, (0, 0, 2000, 500), "", "image"),
            _layout(1, (40, 2000, 920, 2125),
                    'An old catalogue contains a photograph labeled'),
            _layout(2, (1040, 300, 1970, 2150),
                    '"An ancient house," although its location is unknown.'),
        ]
        paragraphs = [item for item in Jointer([(1, layouts)]).execute()
                      if isinstance(item, TextFlowItem)]
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(len(paragraphs[0].children), 2)

    def test_column_wrap_uses_existing_paragraph_guards(self):
        for ending, ref in [("A complete sentence.", "text"),
                            ("An unfinished heading", "sub_title")]:
            with self.subTest(ending=ending):
                layouts = [
                    _layout(0, (0, 0, 2000, 500), "", "image"),
                    _layout(1, (0, 600, 950, 1800), ending, ref),
                    _layout(2, (1050, 600, 2000, 800), "New paragraph."),
                ]
                paragraphs = [item for item in Jointer([(1, layouts)]).execute()
                              if isinstance(item, TextFlowItem)]
                self.assertEqual(len(paragraphs), 2)

    def test_primary_projection_exposes_column_continuation_in_both_modes(self):
        layouts = [
            _layout(0, (0, 0, 2000, 500), "", "image"),
            _layout(1, (0, 1600, 950, 2400), "The reader complained about"),
            _layout(2, (1050, 600, 2000, 900), "several aspects of the treatment."),
        ]
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                page = Page(1, None, layouts, [], 0, 0)
                target = project_window([None, page, None], footnotes)[1]
                assert target is not None
                self.assertTrue(target.layouts[2].continues_from_previous)
                self.assertEqual([item.source_order for item in target.layouts], [None, 1, 2])

    def test_equally_indented_dialogue_uses_local_column_margin(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "這是一個完整的背景段落。"),
            _layout(1, (80, 230, 700, 270), '孩子說：“我已經回來了！”'),
            _layout(2, (80, 290, 320, 330), "老人又說："),
            _layout(3, (0, 350, 1000, 550), '“下一個完整的回答。”'),
        ]
        result = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(result), 4)

    def test_indented_page_head_after_completed_quotation_is_separate(self):
        previous = [_layout(0, (0, 0, 1000, 200), '老人說：“你一定記得！”')]
        current = [
            _layout(0, (80, 0, 560, 40), "孩子慢慢地笑了。"),
            _layout(1, (0, 60, 1000, 260), '“這是另一個完整的回答。”'),
        ]
        result = list(Jointer([(1, previous), (2, current)]).execute())
        self.assertEqual(len(result), 3)

    def test_column_margin_cannot_split_an_unfinished_word(self):
        previous = [_layout(0, (0, 0, 1000, 200), "他正在走向那條河")]
        current = [
            _layout(0, (80, 0, 560, 40), "街旁邊的小路。"),
            _layout(1, (0, 60, 1000, 260), "這是下一個完整的自然段。"),
        ]
        result = list(Jointer([(1, previous), (2, current)]).execute())
        self.assertEqual(len(result), 2)
        first = result[0]
        assert isinstance(first, TextFlowItem)
        self.assertEqual(len(first.children), 2)

    def test_measured_alignment_is_not_overridden_by_nearby_margin(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "背景描述已經結束。"),
            _layout(1, (80, 230, 700, 270), '有人說：“我們回來了！”'),
            _layout(2, (80, 290, 320, 330), "另一個片段。"),
        ]
        # OCR text length can underestimate printed lines. A pixel observation
        # is stronger than an estimate based on the enclosing column.
        layouts[2].first_line_inset = 0
        result = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(result), 2)

    def test_same_line_attribution_keeps_its_quote_with_local_margin(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "背景描述已經結束。"),
            _layout(1, (80, 230, 400, 270), '“現在就走！”'),
            _layout(2, (420, 230, 950, 270), "他說完便拿起了行李。"),
        ]
        result = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(result), 2)
        paragraph = result[1]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(len(paragraph.children), 2)

    def test_indented_single_line_after_colon_starts_paragraph(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "這段敘述引出了人物接下來要說的話："),
            _layout(1, (60, 230, 300, 250), "“這是下一段。”"),
        ]
        self.assertFalse(_can_join(layouts))

    def test_short_completed_quotation_precedes_new_wider_paragraph(self):
        layouts = [
            _layout(0, (60, 0, 300, 20), "“這是上一段。”"),
            _layout(1, (0, 50, 1000, 250), "旁邊的人開始整理自己的行李，準備回家。"),
        ]
        self.assertFalse(_can_join(layouts))

    def test_indent_cannot_split_unfinished_word(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "他慢慢走到了河"),
            _layout(1, (60, 230, 300, 250), "街旁邊的小路。"),
        ]
        self.assertTrue(_can_join(layouts))

    def test_inline_quoted_term_does_not_end_paragraph(self):
        layouts = [
            _layout(0, (60, 0, 300, 20), "這是一種“方法”"),
            _layout(1, (0, 50, 1000, 250), "而不是另一個獨立的問題。"),
        ]
        self.assertTrue(_can_join(layouts))

    def test_same_line_attribution_is_not_a_new_paragraph(self):
        layouts = [
            _layout(0, (0, 0, 200, 20), "“請等一下。”"),
            _layout(1, (210, 0, 900, 20), "他小聲說，然後走向門邊。"),
        ]
        self.assertTrue(_can_join(layouts))

    def test_image_interposition_keeps_existing_paragraph_join(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "這段敘述引出了人物接下來要說的話："),
            _layout(1, (0, 210, 1000, 400), "", "image"),
            _layout(2, (60, 430, 300, 450), "“這是下一句。”"),
        ]
        paragraphs = list(Jointer([(1, layouts)]).execute())
        self.assertEqual(len(paragraphs), 1)
        paragraph = paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(len(paragraph.children), 3)

    def test_multiline_bbox_does_not_prove_indentation(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "他對大家說：“這件事已經做好了。”"),
            _layout(1, (60, 230, 800, 430), "接下來還有許多事情需要安排，這個框本身不能說明首行縮進。"),
        ]
        self.assertTrue(_can_join(layouts))

    def test_layout_boundary_is_bypassed_across_intervening_asset(self):
        layouts = [
            _layout(0, (0, 0, 1000, 200), "這段敘述引出了人物接下來要說的話："),
            _layout(1, (60, 230, 300, 250), "“這是下一段。”"),
        ]
        self.assertFalse(_can_join(layouts))
        self.assertTrue(_can_join(layouts, across_assets=True))

    def test_detection_does_not_depend_on_render_scale(self):
        for scale in (0.5, 1, 3):
            with self.subTest(scale=scale):
                layouts = [
                    _layout(0, tuple(int(x * scale) for x in (0, 0, 1000, 200)),
                            "這段敘述引出了人物接下來要說的話："),
                    _layout(1, tuple(int(x * scale) for x in (60, 230, 300, 250)),
                            "“這是下一段。”"),
                ]
                self.assertFalse(_can_join(layouts))

    def test_primary_projection_shares_boundary_and_keeps_sources(self):
        layouts = [
            _layout(0, (127, 70, 792, 200), "前面的文字已經結束。"),
            _layout(1, (127, 210, 792, 760), "這段敘述引出了人物接下來要說的話："),
            _layout(2, (190, 780, 592, 804), "“這是下一個完整段落。”"),
        ]
        page = Page(1, None, layouts, [], 0, 0)
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                result = project_window([None, page, None], footnotes)[1]
                assert result is not None
                self.assertFalse(result.layouts[2].continues_from_previous)
                self.assertEqual([x.source_order for x in result.layouts], [0, 1, 2])
                self.assertEqual([x.source.origin.text if x.source.origin else None
                                  for x in result.layouts],
                                 [x.text for x in layouts])
        self.assertEqual(layouts[1].text, "這段敘述引出了人物接下來要說的話：")


if __name__ == "__main__":
    unittest.main()
