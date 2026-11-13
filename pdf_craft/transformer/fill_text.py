"""Plain artist view and symmetric comparison, independent of XML field layout."""
from difflib import SequenceMatcher
import unicodedata

from markdown_it import MarkdownIt

from ..common.whitespace import is_whitespace
from ..formula.comparison import normalize_latex


def plain_translation(text: str) -> str:
    """Best-effort CommonMark rendering. Unknown syntax remains literal data."""
    try:
        parser = MarkdownIt('commonmark', {'html': False}).disable(['escape', 'entity', 'list', 'hr'])
        def content(tokens):
            parts = []
            for token in tokens:
                if token.children is not None:
                    parts.append(content(token.children))
                elif token.type in ('text', 'code_inline', 'code_block', 'fence'):
                    parts.append(token.content)
                elif token.type == 'image':
                    parts.append(token.content)
                elif token.type in ('softbreak', 'hardbreak') or token.block and token.nesting == -1:
                    parts.append('\n')
            return ''.join(parts)
        rendered = content(parser.parse(text))
        return text if _document_text(rendered) == _document_text(text) else rendered
    except Exception:  # This optional optimization cannot reject artist output.
        return text


def _document_text(text: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFC', text) if not is_whitespace(char))


def comparison_text(text: str) -> str:
    """Symmetric comparison only; never an artist or delivery transformation."""
    return _document_text(normalize_latex(text))


def _comparison_edits(expected: str, actual: str):
    # Repetitive text can make SequenceMatcher quadratic even when the sole
    # difference is a missing suffix. Exact shared boundaries need no search.
    prefix, suffix = 0, 0
    common = min(len(expected), len(actual))
    while prefix < common and expected[prefix] == actual[prefix]:
        prefix += 1
    while suffix < common - prefix and expected[-suffix-1] == actual[-suffix-1]:
        suffix += 1
    left_end, right_end = len(expected) - suffix, len(actual) - suffix
    return [(kind, prefix+a, prefix+b, prefix+c, prefix+d)
             for kind, a, b, c, d in SequenceMatcher(
                 None, expected[prefix:left_end], actual[prefix:right_end], autojunk=False).get_opcodes()
             if kind != 'equal']


def comparison_distance(expected: str, actual: str) -> int:
    """Diagnostic repair ranking only; zero never replaces XML/content acceptance."""
    if _document_text(expected) == _document_text(actual):
        return 0
    return sum(b - a + d - c for _, a, b, c, d in _comparison_edits(
        comparison_text(expected), comparison_text(actual)))


def text_difference(expected: str, actual: str, blocks=()) -> str | None:
    # Block ownership is resolved by the caller. Feedback never imposes a field
    # allocation or mutates those blocks; comparison remains whole-string only.
    del blocks
    if _document_text(expected) == _document_text(actual):
        return None
    expected, actual = comparison_text(expected), comparison_text(actual)
    if expected == actual:
        return None
    from .fill_feedback import render_difference
    return render_difference(expected, actual, _comparison_edits(expected, actual))
