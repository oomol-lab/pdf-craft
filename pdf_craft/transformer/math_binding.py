"""Restore source notation only when flattened translated mathematics agrees."""
import re

from ..formula.plain_text import try_latex_to_plain_text
from .whitespace import normalize


def literal_percent(text: str) -> str:
    # Plain translated percentages are literal text, not TeX comments.
    return re.sub(r'(?<!\\)%', r'\\%', text)


def unchanged_formula(source: str, translated: str) -> bool:
    # A flattened percentage sign is literal, not a TeX comment. Do not erase
    # internal whitespace or punctuation to manufacture a successful match.
    if source.strip(' \t\r\n') == translated.strip(' \t\r\n'):
        return True
    old = try_latex_to_plain_text(literal_percent(source))
    new = try_latex_to_plain_text(literal_percent(translated))
    return (old is not None and new is not None and bool(normalize(old, trim=True))
            and normalize(old, trim=True) == normalize(new, trim=True))
