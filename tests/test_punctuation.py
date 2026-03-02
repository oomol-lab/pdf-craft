import unittest

from pdf_craft.expression import ExpressionKind
from pdf_craft.markdown.paragraph import parse_raw_markdown, render_markdown_paragraph
from pdf_craft.sequence.chapter import (
    AssetLayout,
    BlockLayout,
    Chapter,
    InlineExpression,
    ParagraphLayout,
    Reference,
)
from pdf_craft.sequence.punctuation import normalize_punctuation_in_chapter


def _create_paragraph(content, page_index: int = 1, order: int = 0) -> ParagraphLayout:
    return ParagraphLayout(
        ref="text",
        level=-1,
        blocks=[
            BlockLayout(
                page_index=page_index,
                order=order,
                det=(0, 0, 100, 100),
                content=content,
            )
        ],
    )


class TestPunctuationNormalize(unittest.TestCase):
    def test_convert_ascii_punctuation_between_han_chars(self):
        paragraph = _create_paragraph(["这是中文, 这是中文: 也是中文; 对吗? 当然!"])
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(
            paragraph.blocks[0].content[0],
            "这是中文， 这是中文： 也是中文； 对吗？ 当然！",
        )

    def test_skip_english_context(self):
        paragraph = _create_paragraph(["hello, world: test; why? sure!"])
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(
            paragraph.blocks[0].content[0], "hello, world: test; why? sure!"
        )

    def test_skip_mixed_context(self):
        paragraph = _create_paragraph(["中文, English; English,中文"])
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(paragraph.blocks[0].content[0], "中文， English; English,中文")

    def test_colon_requires_han_on_both_sides(self):
        paragraph = _create_paragraph(["中文: English"])
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(paragraph.blocks[0].content[0], "中文: English")

    def test_keep_inline_expression_unchanged(self):
        paragraph = _create_paragraph(
            [
                "公式",
                InlineExpression(kind=ExpressionKind.INLINE_DOLLAR, content="x,y"),
                "结束",
            ]
        )
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        inline_expr = paragraph.blocks[0].content[1]
        assert isinstance(inline_expr, InlineExpression)
        self.assertEqual(inline_expr.content, "x,y")

    def test_apply_to_reference_layouts(self):
        ref_paragraph = _create_paragraph(["注释,内容"], page_index=2, order=1)
        reference = Reference(
            page_index=2,
            order=1,
            mark="[1]",
            layouts=[ref_paragraph],
        )
        paragraph = _create_paragraph(["正文", reference], page_index=1, order=1)
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(ref_paragraph.blocks[0].content[0], "注释，内容")

    def test_skip_asset_content_but_normalize_caption(self):
        asset = AssetLayout(
            page_index=1,
            ref="table",
            det=(0, 0, 100, 100),
            title=["标题,内容"],
            content=["<table><tr><td>中文,中文</td></tr></table>"],
            caption=["注释,内容"],
            hash="hash",
        )
        chapter = Chapter(id=1, level=0, layouts=[asset])

        normalize_punctuation_in_chapter(chapter)

        self.assertEqual(asset.title[0], "标题，内容")
        self.assertEqual(asset.content[0], "<table><tr><td>中文,中文</td></tr></table>")
        self.assertEqual(asset.caption[0], "注释，内容")

    def test_convert_punctuation_across_html_tag_boundary(self):
        paragraph = _create_paragraph(parse_raw_markdown("大家<b>好</b>,世界"))
        chapter = Chapter(id=1, level=0, layouts=[paragraph])

        normalize_punctuation_in_chapter(chapter)

        rendered = "".join(
            render_markdown_paragraph(
                children=paragraph.blocks[0].content,
                render_payload=lambda part: [part] if isinstance(part, str) else [],
            )
        )
        self.assertEqual(rendered, "大家<b>好</b>，世界")


if __name__ == "__main__":
    unittest.main()
