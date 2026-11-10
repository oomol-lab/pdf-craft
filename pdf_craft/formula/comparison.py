"""Transactional str -> str normalization of explicitly enclosed mathematics."""
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text

from ..common.whitespace import is_whitespace, normalize_whitespace
from .math_subset import MAX_LENGTH, MathParser


def _openings(text: str, position: int, floor: int) -> tuple[int, list[str]]:
    """Find only adjacent opening wrappers, never search through prose."""
    closings = []
    while position > floor and len(closings) < 16:
        while position > floor and is_whitespace(text[position-1]):
            position -= 1
        if position <= floor:
            break
        if position >= floor + 2 and text[position-2:position] in ('\\(', '\\['):
            closings.append('\\)' if text[position-1] == '(' else '\\]')
            position -= 2
        elif text[position-1:position] == '$' and (position < 2 or text[position-2] != '\\'):
            opening = '$$' if position >= floor+2 and text[position-2:position] == '$$' else '$'
            closings.append(opening)
            position -= len(opening)
        else:
            break
    return position, closings


def _closing(text: str, position: int, closings: list[str]) -> int | None:
    for closing in closings:
        while position < len(text) and is_whitespace(text[position]):
            position += 1
        if not text.startswith(closing, position):
            return None
        position += len(closing)
    return position


def normalize_latex(text: str) -> str:
    """Try a bounded math prefix, then confirm its immediate paired wrappers.

    Parsing does not scan for a closing dollar, does not accept prose, and never
    removes an isolated symbol. Any uncertain conversion rolls back the entire
    optional pass. There is no partial conversion left behind on failure.
    """
    try:
        converter = LatexNodes2Text()
        output, cursor, literal = [], 0, 0
        while cursor < len(text):
            if is_whitespace(text[cursor]) or text[cursor] in '$':
                cursor += 1
                continue
            start, closings = _openings(text, cursor, literal)
            if not closings:
                cursor += 1
                continue
            parser = MathParser(text[cursor:cursor+MAX_LENGTH], converter)
            body = parser.expression()
            end = _closing(text, cursor + parser.pos, closings)
            if end is None:
                # No established enclosing expression: keep it as ordinary text.
                cursor += max(1, parser.pos)
                continue
            output.extend((text[literal:start], '$' + body + '$'))
            cursor = literal = end
        output.append(text[literal:])
        return ''.join(output)
    except Exception:  # An optimization must never reject or partly edit text.
        return text


def plain_formula(text: str) -> str | None:
    """Stringify one explicitly filled formula, retaining mathematical groups.

    Unknown constructs cannot become evidence for restoring source content.
    """
    try:
        value = normalize_latex(text).strip()
        if value.startswith('$') and value.endswith('$'):
            value = value[1:-1]
        parser = MathParser(value, LatexNodes2Text())
        body = parser.expression()
        parser.whitespace()
        if parser.pos != len(value) or len(value) >= MAX_LENGTH:
            return None
        # Generic latex2text loses grouping: frac{1}{2+3} and frac{1}{2}+3
        # can both become 1/2+3. Keep the subset's unambiguous flat spelling.
        return body
    except Exception:
        return None


def same_formula(source: str, translated: str) -> bool:
    """Source reuse only, after fill has selected the existing formula container."""
    left, right = plain_formula(source), plain_formula(translated)
    if left is None or right is None:
        return False
    def normalized(value):
        return normalize_whitespace(unicodedata.normalize('NFC', value), trim=True)
    return normalized(left) == normalized(right)
