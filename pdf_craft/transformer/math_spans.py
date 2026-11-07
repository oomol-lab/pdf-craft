"""Lexical math boundaries, retaining TeX groups and control symbols.

This does not validate TeX or alter artist text. A new same-kind, same-level
opener abandons an unfinished span; nested spans belong to a complete
outer formula, or remain available when that outer formula never completes.
"""
from dataclasses import dataclass
import re

_SPAN = re.compile(r'[\s\S]*')
_ARGUMENT = re.compile(r'\s*\{')


@dataclass
class _Frame:
    start: int
    depth: int
    close: str
    slash_width: int
    control_width: int
    checkpoint: int
    environments: int = 0


def math_matches(text: str) -> list[re.Match[str]]:
    """Return ordinary Match objects with offsets in the unmodified full text."""
    spans: list[tuple[int, int]] = []
    frames: list[_Frame] = []
    depth = 0
    cursor = 0
    comment_end = -1
    while cursor < len(text):
        start = cursor
        char = text[cursor]
        width = 1
        if char == '\\':
            while cursor < len(text) and text[cursor] == '\\':
                cursor += 1
            width = cursor-start
            symbol = text[cursor:cursor+1]
            candidate_close = (frames and '\\'+symbol == frames[-1].close
                               and width == frames[-1].slash_width)
            if frames and not candidate_close and (frames[-1].environments or symbol not in ('(', '[')):
                # Carry the outer escape width for overescaped delimiters.
                # Consume complete row commands before interpreting the last
                # control symbol. In an alignment, \\(x) is a row followed
                # by ordinary (x). Outside an environment an opener may instead
                # be overescaped; inside braces it is only tentative, since a
                # completed outer formula will own all such nested candidates.
                row_width = 2*frames[-1].control_width
                start += width//row_width*row_width
                width = cursor-start
                if not width:
                    continue
            if cursor == len(text):
                break
            char = text[cursor]
            cursor += 1
            if char not in '([])':
                # Escaped braces/dollars are literal control symbols. Control
                # words need no expansion: their following brace groups are
                # scanned normally on subsequent iterations.
                if char.isalpha():
                    word_start = cursor-1
                    while cursor < len(text) and text[cursor].isalpha():
                        cursor += 1
                    word = text[word_start:cursor]
                    if frames and start >= comment_end:
                        if word in ('bgroup', 'begingroup'):
                            depth += 1
                        elif word in ('egroup', 'endgroup'):
                            depth = max(0, depth-1)
                            while frames and frames[-1].depth > depth:
                                frames.pop()
                        elif word == 'begin' and _ARGUMENT.match(text, cursor):
                            frames[-1].environments += 1
                        elif word == 'end' and _ARGUMENT.match(text, cursor):
                            frames[-1].environments = max(0, frames[-1].environments-1)
                continue
            token = '\\'+char
        elif char == '$':
            token = '$$' if text.startswith('$$', cursor) else '$'
            cursor += len(token)
            width = frames[-1].control_width if frames else 1
        else:
            cursor += 1
            if frames:
                if char == '%' and start >= comment_end:
                    comment_end = text.find('\n', cursor)
                    if comment_end < 0:
                        comment_end = len(text)
                elif start < comment_end:
                    continue
                elif char == '{':
                    depth += 1
                elif char == '}':
                    depth = max(0, depth-1)
                    while frames and frames[-1].depth > depth:
                        frames.pop()
            continue

        if frames and (depth == frames[-1].depth or start < comment_end) and token == frames[-1].close:
            frame = frames.pop()
            # A complete outer span owns its nested math. Each nested span is
            # removed once; unfinished frames leave completed children intact.
            spans[frame.checkpoint:] = [(frame.start, cursor)]
            # Remain permissive about artist percentages such as $96%$:
            # closing delimiters retain their literal transport interpretation.
            if not frames:
                depth = 0
                comment_end = -1
            continue
        if start < comment_end:
            # Commented openers/braces cannot abandon the active formula.
            continue
        close = {'\\(': '\\)', '\\[': '\\]', '$': '$', '$$': '$$'}.get(token)
        if close is not None:
            if frames and depth == frames[-1].depth and close == frames[-1].close:
                frames.pop()
            if not frames:
                depth = 0
            # A tentative nested opener must not change the surrounding TeX
            # escape convention (e.g. a row followed by parentheses in text).
            control_width = frames[-1].control_width if frames else width
            frames.append(_Frame(start, depth, close, width, control_width, len(spans)))

    result = []
    for start, end in spans:
        match = _SPAN.match(text, start, end)
        assert match is not None
        result.append(match)
    return result
