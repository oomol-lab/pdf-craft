import unittest

from pdf_craft.sequence.chapter import InlineExpression
from pdf_craft.sequence.jointer import (
    _AssetHolder,
    _normalize_equation,
    _normalize_table,
    _parse_block_content,
)


class TestNormalizeEquation(unittest.TestCase):
    """测试 normalize_equation 函数"""

    def test_equation_with_bracket_notation(self):
        r"""测试 \[...\] 格式的 LaTeX 代码"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"This is a formula: \[x^2 + y^2 = z^2\] and some text after",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"x^2 + y^2 = z^2")
        self.assertEqual(layout.title, "This is a formula: ")
        self.assertEqual(layout.caption, " and some text after")

    def test_equation_with_double_dollar(self):
        """测试 $$...$$ 格式的 LaTeX 代码"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Equation: $$E = mc^2$$ Einstein's formula",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "E = mc^2")
        self.assertEqual(layout.title, "Equation: ")
        self.assertEqual(layout.caption, " Einstein's formula")

    def test_equation_with_parenthesis_notation(self):
        r"""测试 \(...\) 格式的 LaTeX 代码"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Inline math \(a + b = c\) in text",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"a + b = c")
        self.assertEqual(layout.title, "Inline math ")
        self.assertEqual(layout.caption, " in text")

    def test_equation_with_single_dollar(self):
        """测试 $...$ 格式的 LaTeX 代码"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Simple $x = y$ equation",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "x = y")
        self.assertEqual(layout.title, "Simple ")
        self.assertEqual(layout.caption, " equation")

    def test_equation_without_title(self):
        """测试没有 title 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"\[a^2 + b^2 = c^2\] This is caption",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"a^2 + b^2 = c^2")
        self.assertIsNone(layout.title)
        self.assertEqual(layout.caption, " This is caption")

    def test_equation_without_caption(self):
        """测试没有 caption 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Title text \[f(x) = x^2\]",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"f(x) = x^2")
        self.assertEqual(layout.title, "Title text ")
        self.assertIsNone(layout.caption)

    def test_equation_only_latex(self):
        """测试只有 LaTeX 代码的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"\[\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}\]",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(
            layout.content, r"\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}"
        )
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_equation_with_existing_title(self):
        """测试已存在 title 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title="Existing title",
            content=r"More title \[x = y\] caption text",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"x = y")
        self.assertEqual(layout.title, "Existing titleMore title ")
        self.assertEqual(layout.caption, " caption text")

    def test_equation_with_existing_caption(self):
        """测试已存在 caption 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Title \[a = b\] more caption",
            caption="Existing caption",
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"a = b")
        self.assertEqual(layout.title, "Title ")
        self.assertEqual(layout.caption, " more captionExisting caption")

    def test_equation_empty_content(self):
        """测试空内容"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_equation_no_latex_found(self):
        """测试没有找到 LaTeX 代码的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Just plain text without any latex",
            caption=None,
            hash=None,
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "Just plain text without any latex")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)


class TestNormalizeTable(unittest.TestCase):
    """测试 normalize_table 函数"""

    def test_table_with_title_and_caption(self):
        """测试带有 title 和 caption 的表格"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Table 1: Sample Data<table><tr><td>A</td><td>B</td></tr></table>Source: Test",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>A</td><td>B</td></tr></table>")
        self.assertEqual(layout.title, "Table 1: Sample Data")
        self.assertEqual(layout.caption, "Source: Test")

    def test_table_without_title(self):
        """测试没有 title 的表格"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="<table><tr><td>X</td></tr></table>Note: Important",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>X</td></tr></table>")
        self.assertIsNone(layout.title)
        self.assertEqual(layout.caption, "Note: Important")

    def test_table_without_caption(self):
        """测试没有 caption 的表格"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Results:<table><tr><td>1</td><td>2</td></tr></table>",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>1</td><td>2</td></tr></table>")
        self.assertEqual(layout.title, "Results:")
        self.assertIsNone(layout.caption)

    def test_table_only_html(self):
        """测试只有 HTML 的表格"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="<table><tr><td>Data</td></tr></table>",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>Data</td></tr></table>")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_table_with_attributes(self):
        """测试带属性的表格标签"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content='Title<table class="data" id="t1"><tr><td>A</td></tr></table>Caption',
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(
            layout.content, '<table class="data" id="t1"><tr><td>A</td></tr></table>'
        )
        self.assertEqual(layout.title, "Title")
        self.assertEqual(layout.caption, "Caption")

    def test_table_multiline(self):
        """测试多行的表格"""
        content = """Description
<table>
  <tr>
    <td>Row 1</td>
  </tr>
  <tr>
    <td>Row 2</td>
  </tr>
</table>
Footer text"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content=content,
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertIn("<table>", layout.content)
        self.assertIn("</table>", layout.content)
        self.assertEqual(layout.title, "Description")
        self.assertEqual(layout.caption, "Footer text")

    def test_table_case_insensitive(self):
        """测试大小写不敏感"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Text<TABLE><TR><TD>Data</TD></TR></TABLE>More",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<TABLE><TR><TD>Data</TD></TR></TABLE>")
        self.assertEqual(layout.title, "Text")
        self.assertEqual(layout.caption, "More")

    def test_table_with_existing_title(self):
        """测试已存在 title 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title="Existing title",
            content="More title<table><tr><td>A</td></tr></table>Caption",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>A</td></tr></table>")
        self.assertEqual(layout.title, "Existing title\nMore title")
        self.assertEqual(layout.caption, "Caption")

    def test_table_with_existing_caption(self):
        """测试已存在 caption 的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Title<table><tr><td>B</td></tr></table>More caption",
            caption="Existing caption",
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>B</td></tr></table>")
        self.assertEqual(layout.title, "Title")
        self.assertEqual(layout.caption, "More caption\nExisting caption")

    def test_table_empty_content(self):
        """测试空内容"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_table_no_table_found(self):
        """测试没有找到表格的情况"""
        layout = _AssetHolder(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Just text without any table",
            caption=None,
            hash=None,
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "Just text without any table")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)


class TestParseLineContent(unittest.TestCase):
    """测试 _parse_line_content 函数"""

    def test_plain_text_only(self):
        """测试纯文本"""
        result = _parse_block_content("This is plain text")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "This is plain text")

    def test_single_dollar_inline_formula(self):
        """测试单美元符号行内公式"""
        result = _parse_block_content("Einstein's formula $E = mc^2$ is famous")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Einstein's formula ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "E = mc^2")
        self.assertEqual(result[2], " is famous")

    def test_double_dollar_formula(self):
        """测试双美元符号公式"""
        result = _parse_block_content("The formula $$x^2 + y^2 = z^2$$ is Pythagorean")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "The formula ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "x^2 + y^2 = z^2")
        self.assertEqual(result[2], " is Pythagorean")

    def test_parenthesis_inline_formula(self):
        r"""测试 \( ... \) 行内公式"""
        result = _parse_block_content(r"Inline \(a + b = c\) formula")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Inline ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "a + b = c")
        self.assertEqual(result[2], " formula")

    def test_bracket_display_formula(self):
        r"""测试 \[ ... \] 显示公式"""
        result = _parse_block_content(r"Display \[f(x) = x^2\] formula")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Display ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "f(x) = x^2")
        self.assertEqual(result[2], " formula")

    def test_multiple_formulas(self):
        """测试多个公式"""
        result = _parse_block_content("First $x$ and second $y$ formulas")
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], "First ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "x")
        self.assertEqual(result[2], " and second ")
        self.assertIsInstance(result[3], InlineExpression)
        assert isinstance(result[3], InlineExpression)
        self.assertEqual(result[3].content, "y")
        self.assertEqual(result[4], " formulas")

    def test_empty_string(self):
        """测试空字符串"""
        result = _parse_block_content("")
        self.assertEqual(len(result), 0)

    def test_formula_only(self):
        """测试只有公式"""
        result = _parse_block_content("$x = y$")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], InlineExpression)
        assert isinstance(result[0], InlineExpression)
        self.assertEqual(result[0].content, "x = y")

    def test_escaped_dollar(self):
        r"""测试转义的美元符号"""
        result = _parse_block_content(r"Price is \$100")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Price is $100")

    def test_mixed_delimiters(self):
        r"""测试混合定界符"""
        result = _parse_block_content(r"Mix $a$ and \(b\) and $$c$$")
        self.assertEqual(len(result), 6)
        self.assertEqual(result[0], "Mix ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, "a")
        self.assertEqual(result[2], " and ")
        self.assertIsInstance(result[3], InlineExpression)
        assert isinstance(result[3], InlineExpression)
        self.assertEqual(result[3].content, "b")
        self.assertEqual(result[4], " and ")
        self.assertIsInstance(result[5], InlineExpression)
        assert isinstance(result[5], InlineExpression)
        self.assertEqual(result[5].content, "c")

    def test_complex_latex_content(self):
        """测试复杂的 LaTeX 内容"""
        result = _parse_block_content(
            r"The integral $\int_0^\infty e^{-x^2} dx$ converges"
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "The integral ")
        self.assertIsInstance(result[1], InlineExpression)
        assert isinstance(result[1], InlineExpression)
        self.assertEqual(result[1].content, r"\int_0^\infty e^{-x^2} dx")
        self.assertEqual(result[2], " converges")


if __name__ == "__main__":
    unittest.main()
