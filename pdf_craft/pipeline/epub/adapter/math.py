from xml.etree.ElementTree import Element

# 运算符映射表
_OPERATOR_MAP = {
    "→": r"\rightarrow",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "×": r"\times",
    "·": r"\cdot",
    "÷": r"\div",
    "±": r"\pm",
    "∓": r"\mp",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "≈": r"\approx",
    "∞": r"\infty",
    "∫": r"\int",
    "∑": r"\sum",
    "∏": r"\prod",
    "√": r"\sqrt",
    "∂": r"\partial",
    "∇": r"\nabla",
    "∈": r"\in",
    "∉": r"\notin",
    "⊂": r"\subset",
    "⊃": r"\supset",
    "⊆": r"\subseteq",
    "⊇": r"\supseteq",
    "∪": r"\cup",
    "∩": r"\cap",
    "∅": r"\emptyset",
    "∀": r"\forall",
    "∃": r"\exists",
    "¬": r"\neg",
    "∧": r"\land",
    "∨": r"\lor",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\epsilon",
    "θ": r"\theta",
    "λ": r"\lambda",
    "μ": r"\mu",
    "π": r"\pi",
    "σ": r"\sigma",
    "φ": r"\phi",
    "ω": r"\omega",
    "Δ": r"\Delta",
    "Σ": r"\Sigma",
    "Ω": r"\Omega",
}


def xml_to_latex(element: Element) -> str:
    tag = element.tag

    # 根据元素类型进行转换
    if tag == "math":
        # 根元素，只处理子元素
        return "".join(xml_to_latex(child) for child in element)

    elif tag == "mrow":
        # 分组元素，递归处理所有子元素
        return "".join(xml_to_latex(child) for child in element)

    elif tag == "mi":
        # 标识符（变量名）
        text = element.text or ""
        # 多字符标识符用 \mathrm
        if len(text) > 1:
            return f"\\mathrm{{{text}}}"
        return text

    elif tag == "mn":
        # 数字
        return element.text or ""

    elif tag == "mo":
        # 运算符
        text = (element.text or "").strip()
        return _OPERATOR_MAP.get(text, text)

    elif tag == "mfrac":
        # 分数
        children = list(element)
        if len(children) >= 2:
            numerator = xml_to_latex(children[0])
            denominator = xml_to_latex(children[1])
            return f"\\frac{{{numerator}}}{{{denominator}}}"
        return ""

    elif tag == "msub":
        # 下标
        children = list(element)
        if len(children) >= 2:
            base = xml_to_latex(children[0])
            subscript = xml_to_latex(children[1])
            return f"{base}_{{{subscript}}}"
        return ""

    elif tag == "msup":
        # 上标
        children = list(element)
        if len(children) >= 2:
            base = xml_to_latex(children[0])
            superscript = xml_to_latex(children[1])
            return f"{base}^{{{superscript}}}"
        return ""

    elif tag == "msubsup":
        # 同时有上下标
        children = list(element)
        if len(children) >= 3:
            base = xml_to_latex(children[0])
            subscript = xml_to_latex(children[1])
            superscript = xml_to_latex(children[2])
            return f"{base}_{{{subscript}}}^{{{superscript}}}"
        return ""

    elif tag == "msqrt":
        # 平方根
        content = "".join(xml_to_latex(child) for child in element)
        return f"\\sqrt{{{content}}}"

    elif tag == "mroot":
        # n次根
        children = list(element)
        if len(children) >= 2:
            base = xml_to_latex(children[0])
            index = xml_to_latex(children[1])
            return f"\\sqrt[{index}]{{{base}}}"
        return ""

    elif tag == "munder":
        # 下方符号
        children = list(element)
        if len(children) >= 2:
            base = xml_to_latex(children[0])
            under = xml_to_latex(children[1])
            return f"\\underset{{{under}}}{{{base}}}"
        return ""

    elif tag == "mover":
        # 上方符号
        children = list(element)
        if len(children) >= 2:
            base = xml_to_latex(children[0])
            over = xml_to_latex(children[1])
            return f"\\overset{{{over}}}{{{base}}}"
        return ""

    elif tag == "munderover":
        # 上下方符号
        children = list(element)
        if len(children) >= 3:
            base = xml_to_latex(children[0])
            under = xml_to_latex(children[1])
            over = xml_to_latex(children[2])
            # 特殊处理求和、积分等
            base_str = base.strip()
            if base_str in (r"\sum", r"\int", r"\prod"):
                return f"{base}_{{{under}}}^{{{over}}}"
            return f"\\overset{{{over}}}{{\\underset{{{under}}}{{{base}}}}}"
        return ""

    elif tag == "mtext":
        # 文本
        text = element.text or ""
        return f"\\text{{{text}}}"

    elif tag == "mspace":
        # 空格
        return r"\,"

    elif tag == "mtable":
        # 表格/矩阵
        rows = [xml_to_latex(child) for child in element if child.tag.endswith("mtr")]
        return f"\\begin{{array}}{{{rows[0].count('&') + 1}}}\n" + "\\\\\n".join(rows) + "\n\\end{array}"

    elif tag == "mtr":
        # 表格行
        cells = [xml_to_latex(child) for child in element if child.tag.endswith("mtd")]
        return " & ".join(cells)

    elif tag == "mtd":
        # 表格单元格
        return "".join(xml_to_latex(child) for child in element)

    else:
        # 未知元素，递归处理子元素
        return "".join(xml_to_latex(child) for child in element)
