"""Best-effort, dependency-light text fallback for inline LaTeX."""

from pylatexenc.latex2text import LatexNodes2Text


_CONVERTER = LatexNodes2Text()


def latex_to_plain_text(latex_content: str) -> str:
    """Return readable Unicode text, retaining a visible fallback on parse errors."""
    try:
        return _CONVERTER.latex_to_text(latex_content)
    except Exception:  # pragma: no cover - converter parser errors are input dependent.
        return f"[{latex_content}]"
