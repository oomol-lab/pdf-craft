import unittest
from pdf_craft.sequence.jointer import _normalize_equation, _normalize_table, AssetLayout


class TestNormalizeEquation(unittest.TestCase):
    """测试 normalize_equation 函数"""

    def test_equation_with_bracket_notation(self):
        r"""测试 \[...\] 格式的 LaTeX 代码"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"This is a formula: \[x^2 + y^2 = z^2\] and some text after",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[x^2 + y^2 = z^2\]")
        self.assertEqual(layout.title, "This is a formula:")
        self.assertEqual(layout.caption, "and some text after")

    def test_equation_with_double_dollar(self):
        """测试 $$...$$ 格式的 LaTeX 代码"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Equation: $$E = mc^2$$ Einstein's formula",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "$$E = mc^2$$")
        self.assertEqual(layout.title, "Equation:")
        self.assertEqual(layout.caption, "Einstein's formula")

    def test_equation_with_parenthesis_notation(self):
        r"""测试 \(...\) 格式的 LaTeX 代码"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Inline math \(a + b = c\) in text",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\(a + b = c\)")
        self.assertEqual(layout.title, "Inline math")
        self.assertEqual(layout.caption, "in text")

    def test_equation_with_single_dollar(self):
        """测试 $...$ 格式的 LaTeX 代码"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Simple $x = y$ equation",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "$x = y$")
        self.assertEqual(layout.title, "Simple")
        self.assertEqual(layout.caption, "equation")

    def test_equation_without_title(self):
        """测试没有 title 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"\[a^2 + b^2 = c^2\] This is caption",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[a^2 + b^2 = c^2\]")
        self.assertIsNone(layout.title)
        self.assertEqual(layout.caption, "This is caption")

    def test_equation_without_caption(self):
        """测试没有 caption 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Title text \[f(x) = x^2\]",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[f(x) = x^2\]")
        self.assertEqual(layout.title, "Title text")
        self.assertIsNone(layout.caption)

    def test_equation_only_latex(self):
        """测试只有 LaTeX 代码的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"\[\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}\]",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}\]")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_equation_with_existing_title(self):
        """测试已存在 title 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title="Existing title",
            content=r"More title \[x = y\] caption text",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[x = y\]")
        self.assertEqual(layout.title, "Existing title\nMore title")
        self.assertEqual(layout.caption, "caption text")

    def test_equation_with_existing_caption(self):
        """测试已存在 caption 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content=r"Title \[a = b\] more caption",
            caption="Existing caption",
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, r"\[a = b\]")
        self.assertEqual(layout.title, "Title")
        self.assertEqual(layout.caption, "Existing caption\nmore caption")

    def test_equation_empty_content(self):
        """测试空内容"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_equation_no_latex_found(self):
        """测试没有找到 LaTeX 代码的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="equation",
            det=(0, 0, 100, 100),
            title=None,
            content="Just plain text without any latex",
            caption=None,
            hash=None
        )
        _normalize_equation(layout)
        self.assertEqual(layout.content, "Just plain text without any latex")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)


class TestNormalizeTable(unittest.TestCase):
    """测试 normalize_table 函数"""

    def test_table_with_title_and_caption(self):
        """测试带有 title 和 caption 的表格"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Table 1: Sample Data<table><tr><td>A</td><td>B</td></tr></table>Source: Test",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>A</td><td>B</td></tr></table>")
        self.assertEqual(layout.title, "Table 1: Sample Data")
        self.assertEqual(layout.caption, "Source: Test")

    def test_table_without_title(self):
        """测试没有 title 的表格"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="<table><tr><td>X</td></tr></table>Note: Important",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>X</td></tr></table>")
        self.assertIsNone(layout.title)
        self.assertEqual(layout.caption, "Note: Important")

    def test_table_without_caption(self):
        """测试没有 caption 的表格"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Results:<table><tr><td>1</td><td>2</td></tr></table>",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>1</td><td>2</td></tr></table>")
        self.assertEqual(layout.title, "Results:")
        self.assertIsNone(layout.caption)

    def test_table_only_html(self):
        """测试只有 HTML 的表格"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="<table><tr><td>Data</td></tr></table>",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>Data</td></tr></table>")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_table_with_attributes(self):
        """测试带属性的表格标签"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content='Title<table class="data" id="t1"><tr><td>A</td></tr></table>Caption',
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, '<table class="data" id="t1"><tr><td>A</td></tr></table>')
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
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content=content,
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertIn("<table>", layout.content)
        self.assertIn("</table>", layout.content)
        self.assertEqual(layout.title, "Description")
        self.assertEqual(layout.caption, "Footer text")

    def test_table_case_insensitive(self):
        """测试大小写不敏感"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Text<TABLE><TR><TD>Data</TD></TR></TABLE>More",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<TABLE><TR><TD>Data</TD></TR></TABLE>")
        self.assertEqual(layout.title, "Text")
        self.assertEqual(layout.caption, "More")

    def test_table_with_existing_title(self):
        """测试已存在 title 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title="Existing title",
            content="More title<table><tr><td>A</td></tr></table>Caption",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>A</td></tr></table>")
        self.assertEqual(layout.title, "Existing title\nMore title")
        self.assertEqual(layout.caption, "Caption")

    def test_table_with_existing_caption(self):
        """测试已存在 caption 的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Title<table><tr><td>B</td></tr></table>More caption",
            caption="Existing caption",
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "<table><tr><td>B</td></tr></table>")
        self.assertEqual(layout.title, "Title")
        self.assertEqual(layout.caption, "Existing caption\nMore caption")

    def test_table_empty_content(self):
        """测试空内容"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)

    def test_table_no_table_found(self):
        """测试没有找到表格的情况"""
        layout = AssetLayout(
            page_index=0,
            ref="table",
            det=(0, 0, 100, 100),
            title=None,
            content="Just text without any table",
            caption=None,
            hash=None
        )
        _normalize_table(layout)
        self.assertEqual(layout.content, "Just text without any table")
        self.assertIsNone(layout.title)
        self.assertIsNone(layout.caption)


if __name__ == "__main__":
    unittest.main()
