"""Transactional string comparison view for mathematical spelling and wrappers."""
import re
import unicodedata

from pylatexenc.latex2text import LatexNodes2Text

from ..common.whitespace import is_whitespace, normalize_whitespace
from .math_subset import MAX_LENGTH, MathParser, MathSyntaxError


def _left_wrappers(text: str, position: int, floor: int) -> tuple[int, int]:
    """Find only adjacent opening wrappers, never search through prose."""
    count = 0
    while position > floor and count < 16:
        previous = position
        while position > floor and is_whitespace(text[position-1]):
            position -= 1
        if position <= floor:
            position = previous
            break
        if position >= floor + 2 and text[position-2:position] in ('\\(', '\\['):
            position -= 2
        elif text[position-1:position] == '$' and (position < 2 or text[position-2] != '\\'):
            opening = '$$' if position >= floor+2 and text[position-2:position] == '$$' else '$'
            position -= len(opening)
        else:
            position = previous
            break
        count += 1
    return position, count


def normalize_latex(text: str) -> str:
    """Recognize math first and ignore its adjacent, even one-sided wrappers.

    Parsing does not scan for a closing dollar, does not accept prose, and never
    removes an isolated non-mathematical symbol. Any uncertain conversion rolls back the entire
    optional pass. There is no partial conversion left behind on failure.
    """
    try:
        converter = LatexNodes2Text()
        output, cursor, literal = [], 0, 0
        while cursor < len(text):
            if is_whitespace(text[cursor]) or text[cursor] in '$':
                cursor += 1
                continue
            if text.startswith(('\\(', '\\)', '\\[', '\\]'), cursor):
                cursor += 2
                continue
            start, opening_count = _left_wrappers(text, cursor, literal)
            parser = MathParser(text[cursor:cursor+MAX_LENGTH], converter, comparison=True)
            try:
                body = parser.expression()
            except MathSyntaxError:
                if opening_count or text[cursor] == '\\':
                    raise
                word = re.match(r'[A-Za-z]+', text[cursor:])
                cursor += len(word[0]) if word else 1
                continue
            if parser.pos >= MAX_LENGTH:
                raise MathSyntaxError('Comparison expression exceeds its optional bound')
            end = cursor + parser.pos
            if not (opening_count or parser.has_delimiters):
                cursor = max(cursor + 1, end)
                continue
            # Keep prose spacing outside the parsed prefix; content comparison
            # separately handles the shared Unicode whitespace policy.
            trailing = end
            while trailing > cursor and is_whitespace(text[trailing-1]):
                trailing -= 1
            output.extend((text[literal:start], body, text[trailing:end]))
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
