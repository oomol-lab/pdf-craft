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


def text_difference(expected: str, actual: str, blocks=()) -> str | None:
    raw_actual = actual
    # Optional mathematical interpretation cannot invalidate existing text equivalence.
    if _document_text(expected) == _document_text(actual):
        return None
    expected, actual = comparison_text(expected), comparison_text(actual)
    if expected == actual:
        return None
    edits = [item for item in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes()
             if item[0] != 'equal']
    missing = sum(end-start for _, start, end, _, _ in edits)
    added = sum(end-start for _, _, _, start, end in edits)
    names = {'delete': 'missing', 'insert': 'extra', 'replace': 'different'}
    details = '\n'.join(f'{names[kind]}: expected[{a}:{b}]={expected[a:b][:6000]!r}; '
                        f'candidate[{c}:{d}]={actual[c:d][:600]!r}; '
                        f'expected context={expected[max(0,a-50):min(len(expected),b+50)]!r}; '
                        f'candidate context={actual[max(0,c-50):min(len(actual),d+50)]!r}'
                        for kind, a, b, c, d in edits[:10])
    message = f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
    if actual != _document_text(raw_actual):
        # LaTeX normalization need not be compositional across XML fields.
        # Keep the exact whole-string diff, not guessed raw-field offsets.
        return message
    cursor, locations = 0, []
    for block in blocks:
        end = cursor + len(comparison_text(''.join(block.itertext())))
        if any((cursor <= start < end) if start == finish else (cursor < finish and start < end)
               for _, _, _, start, finish in edits):
            whole_extra = end > cursor and any(kind == 'insert' and start <= cursor and end <= finish
                                               for kind, _, _, start, finish in edits)
            locations.append(f'block id={block.get("id")}: candidate[{cursor}:{end}], '
                             f'entire_block_extra={whole_extra}, text={"".join(block.itertext())[:200]!r}')
        cursor = end
    prefix = ''
    def fields(node, path):
        yield path + '.text', node.text or ''
        for index, child in enumerate(node):
            child_path = f'{path}/{child.tag}[{index}]'
            yield from fields(child, child_path)
            yield child_path + '.tail', child.tail or ''
    for block in blocks:
        for path, value in fields(block, f'{block.tag}#{block.get("id")}'):
            start = len(comparison_text(prefix))
            prefix += value
            end = len(comparison_text(prefix))
            if end <= start:
                continue
            if any((start <= left < end) if left == right else (start < right and left < end)
                   for _, _, _, left, right in edits):
                extra = any(kind == 'insert' and left <= start and end <= right
                            for kind, _, _, left, right in edits)
                locations.append(f'field={path}, candidate[{start}:{end}], '
                                 f'entire_field_extra={extra}, value={value[:300]!r}')
    if locations:
        details += '\n' + '\n'.join(locations[:12])
    return f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
