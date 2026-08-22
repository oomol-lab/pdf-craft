from pylatexenc.latex2text import LatexNodes2Text

_converter = LatexNodes2Text()


def latex_to_plain_text(latex_content: str) -> str:
    try:
        return _converter.latex_to_text(latex_content)
    except Exception:
        return f"[{latex_content}]"
