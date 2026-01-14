import unittest

from pdf_craft.expression import ExpressionKind, parse_latex_expressions


class TestParseLatexExpressions(unittest.TestCase):
    """测试 parse_latex_expressions 函数"""

    def test_inline_dollar(self):
        """测试 $ ... $ 格式的行内公式"""
        text = "This is $x^2$ inline"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "This is ")
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[1].content, "x^2")
        self.assertEqual(result[2].kind, ExpressionKind.TEXT)
        self.assertEqual(result[2].content, " inline")

    def test_display_double_dollar(self):
        """测试 $$ ... $$ 格式的显示公式"""
        text = "Equation: $$E = mc^2$$ end"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Equation: ")
        self.assertEqual(result[1].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[1].content, "E = mc^2")
        self.assertEqual(result[2].kind, ExpressionKind.TEXT)
        self.assertEqual(result[2].content, " end")

    def test_inline_paren(self):
        r"""测试 \( ... \) 格式的行内公式"""
        text = r"Text \(a + b\) more"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Text ")
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_PAREN)
        self.assertEqual(result[1].content, "a + b")
        self.assertEqual(result[2].kind, ExpressionKind.TEXT)
        self.assertEqual(result[2].content, " more")

    def test_display_bracket(self):
        r"""测试 \[ ... \] 格式的显示公式"""
        text = r"Formula: \[x^2 + y^2 = z^2\] text"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Formula: ")
        self.assertEqual(result[1].kind, ExpressionKind.DISPLAY_BRACKET)
        self.assertEqual(result[1].content, "x^2 + y^2 = z^2")
        self.assertEqual(result[2].kind, ExpressionKind.TEXT)
        self.assertEqual(result[2].content, " text")

    def test_empty_string(self):
        """测试空字符串"""
        text = ""
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 0)

    def test_plain_text_only(self):
        """测试只有普通文本"""
        text = "Just plain text without any formulas"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Just plain text without any formulas")

    def test_formula_only(self):
        """测试只有公式"""
        text = "$x = y$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[0].content, "x = y")

    def test_multiple_formulas(self):
        """测试多个公式"""
        text = "$a$ and $b$ and $c$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[0].content, "a")
        self.assertEqual(result[1].kind, ExpressionKind.TEXT)
        self.assertEqual(result[1].content, " and ")
        self.assertEqual(result[2].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[2].content, "b")
        self.assertEqual(result[3].kind, ExpressionKind.TEXT)
        self.assertEqual(result[3].content, " and ")
        self.assertEqual(result[4].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[4].content, "c")

    def test_escaped_dollar(self):
        r"""测试转义的美元符号 \$"""
        text = r"Price is \$100"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Price is $100")

    def test_double_dollar_priority(self):
        """测试 $$ 优先于单 $ 匹配"""
        text = "$$a$$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[0].content, "a")

    def test_inline_formula_with_newline(self):
        """测试行内公式不允许换行"""
        text = "$a\nb$"
        result = list(parse_latex_expressions(text))
        # 应该解析失败，美元符号被当作普通文本
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "$a\nb$")

    def test_display_formula_with_newline(self):
        """测试显示公式允许换行"""
        text = "$$a\nb$$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[0].content, "a\nb")

    def test_display_bracket_with_newline(self):
        r"""测试 \[ ... \] 显示公式允许换行"""
        text = "\\[a\nb\\]"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_BRACKET)
        self.assertEqual(result[0].content, "a\nb")

    def test_mixed_delimiters(self):
        r"""测试混合不同定界符"""
        text = r"Text $a$ and \(b\) and $$c$$ and \[d\] end"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 9)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[1].content, "a")
        self.assertEqual(result[3].kind, ExpressionKind.INLINE_PAREN)
        self.assertEqual(result[3].content, "b")
        self.assertEqual(result[5].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[5].content, "c")
        self.assertEqual(result[7].kind, ExpressionKind.DISPLAY_BRACKET)
        self.assertEqual(result[7].content, "d")

    def test_unclosed_formula(self):
        """测试未闭合的公式"""
        text = "$unclosed formula"
        result = list(parse_latex_expressions(text))
        # 未找到结束定界符，整个都是文本
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "$unclosed formula")

    def test_empty_formula(self):
        """测试空公式"""
        text = "$$$$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[0].content, "")

    def test_formula_with_spaces(self):
        """测试公式内的空格"""
        text = "$  x  +  y  $"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[0].content, "  x  +  y  ")

    def test_formula_with_special_chars(self):
        r"""测试公式内的特殊字符"""
        text = r"$\alpha + \beta = \gamma$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[0].content, r"\alpha + \beta = \gamma")

    def test_consecutive_formulas(self):
        """测试连续的公式"""
        text = "$a$$b$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[0].content, "a")
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[1].content, "b")

    def test_complex_latex(self):
        r"""测试复杂的 LaTeX 表达式"""
        text = r"$$\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}$$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(
            result[0].content, r"\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}"
        )

    def test_formula_with_dollar_inside(self):
        r"""测试公式内包含美元符号"""
        text = r"$$\$ $$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.DISPLAY_DOUBLE_DOLLAR)
        self.assertEqual(result[0].content, r"\$ ")

    def test_text_with_brackets(self):
        """测试普通文本中的方括号"""
        text = "Text [with brackets] and $x$"
        result = list(parse_latex_expressions(text))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "Text [with brackets] and ")
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[1].content, "x")

    def test_backslash_before_backslash(self):
        r"""测试双反斜杠的情况"""
        text = r"\\$x$"
        result = list(parse_latex_expressions(text))
        # \\ 是转义的反斜杠，$ 是公式开始
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, "\\")
        self.assertEqual(result[1].kind, ExpressionKind.INLINE_DOLLAR)
        self.assertEqual(result[1].content, "x")

    def test_triple_backslash_dollar(self):
        r"""测试三个反斜杠的情况"""
        text = r"\\\$x"
        result = list(parse_latex_expressions(text))
        # \\\ = \\ + \，最后一个 \ 转义 $
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].kind, ExpressionKind.TEXT)
        self.assertEqual(result[0].content, r"\$x")

    def test_roundtrip_plain_text(self):
        """测试纯文本的往返转换"""
        text = "Hello, world! This is plain text."
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_escaped_dollar(self):
        r"""测试转义美元符号的往返转换"""
        text = r"Price is \$100 and \$200"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_double_backslash(self):
        r"""测试双反斜杠的往返转换"""
        text = r"Path: C:\\Users\\Name"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_inline_formula(self):
        """测试行内公式的往返转换"""
        text = r"Formula: $x^2 + y^2 = z^2$"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_display_formula(self):
        """测试显示公式的往返转换"""
        text = r"$$\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}$$"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_inline_paren(self):
        r"""测试 \( ... \) 的往返转换"""
        text = r"Text \(a + b\) more"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_display_bracket(self):
        r"""测试 \[ ... \] 的往返转换"""
        text = r"Formula: \[x^2 + y^2 = z^2\] text"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_mixed_escape_and_formula(self):
        r"""测试混合转义和公式的往返转换"""
        text = r"Cost \$50, formula $x^2$, path C:\\home"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_complex_escape(self):
        r"""测试复杂转义的往返转换

        注意：原始字符串中的 \\$ (两个反斜杠+未配对美元符号) 会被重建为 \\\$
        这是因为解析时 \\$ 被转换为 content 中的 \$（一个反斜杠+美元符号），
        重建时为了安全性，所有美元符号都会被转义，导致重建为 \\\$。
        虽然形式不同，但语义相同（都表示：一个反斜杠字面量 + 美元符号字面量）。
        """
        text = r"\\\$100 means \\\$ sign after backslash"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_backslash_before_formula(self):
        r"""测试公式前的反斜杠往返转换"""
        text = r"\\$x$"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_multiple_formulas_with_escape(self):
        r"""测试多个公式和转义的往返转换"""
        text = r"Price \$10, $a + b$, path C:\\dir, $$x^2$$, cost \$20"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_empty_formula(self):
        """测试空公式的往返转换"""
        text = r"Before $$$$After"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)

    def test_roundtrip_all_delimiters(self):
        r"""测试所有定界符类型的往返转换"""
        text = r"Text $a$ and \(b\) and $$c$$ and \[d\] with \$price and \\path"
        result = list(parse_latex_expressions(text))
        reconstructed = "".join(item.reverse() for item in result)
        self.assertEqual(text, reconstructed)


if __name__ == "__main__":
    unittest.main()
