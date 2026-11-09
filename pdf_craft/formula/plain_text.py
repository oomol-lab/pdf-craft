"""Best-effort, dependency-light text fallback for inline LaTeX."""

from pylatexenc.latex2text import LatexNodes2Text


_CONVERTER = LatexNodes2Text()


def latex_to_plain_text(latex_content: str) -> str:
    """Return readable Unicode text, retaining a visible fallback on parse errors."""
    result = try_latex_to_plain_text(latex_content)
    return result if result is not None else f"[{latex_content}]"


def try_latex_to_plain_text(latex_content: str) -> str | None:
    """Return None on conversion failure so recovery cannot mistake a fallback for evidence."""
    try:
        return _CONVERTER.latex_to_text(latex_content)
    except Exception:  # pragma: no cover - converter parser errors are input dependent.
        return None
