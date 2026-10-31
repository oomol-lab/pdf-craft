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
