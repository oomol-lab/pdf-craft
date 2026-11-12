"""Plain artist view and symmetric comparison, independent of XML field layout."""
from difflib import SequenceMatcher
import unicodedata

from jinja2 import Environment, PackageLoader
from markdown_it import MarkdownIt

from ..common.whitespace import is_whitespace
from ..formula.comparison import normalize_latex


_FEEDBACK = Environment(loader=PackageLoader("pdf_craft.transformer.xml_translator", "data"), autoescape=False)

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
    original = expected
    # Optional mathematical interpretation cannot invalidate existing text equivalence.
    if _document_text(expected) == _document_text(actual):
        return None
    expected, actual = comparison_text(expected), comparison_text(actual)
    if expected == actual:
        return None
    # Repetitive text can make SequenceMatcher quadratic even when the sole
    # difference is a missing suffix. Exact shared boundaries need no search.
    prefix, suffix = 0, 0
    common = min(len(expected), len(actual))
    while prefix < common and expected[prefix] == actual[prefix]:
        prefix += 1
    while suffix < common - prefix and expected[-suffix-1] == actual[-suffix-1]:
        suffix += 1
    left_end, right_end = len(expected) - suffix, len(actual) - suffix
    edits = [(kind, prefix+a, prefix+b, prefix+c, prefix+d)
             for kind, a, b, c, d in SequenceMatcher(
                 None, expected[prefix:left_end], actual[prefix:right_end], autojunk=False).get_opcodes()
             if kind != 'equal']
    missing = sum(end-start for _, start, end, _, _ in edits)
    added = sum(end-start for _, _, _, start, end in edits)
    names = {'delete': 'missing', 'insert': 'extra', 'replace': 'different'}
    details = '\n'.join(f'{names[kind]}: expected[{a}:{b}]={expected[a:b][:6000]!r}; '
                        f'candidate[{c}:{d}]={actual[c:d][:600]!r}; '
                        f'expected context={expected[max(0,a-50):min(len(expected),b+50)]!r}; '
                        f'candidate context={actual[max(0,c-50):min(len(actual),d+50)]!r}'
                        for kind, a, b, c, d in edits[:10])
    # Quote actual artist data, never a field manufactured from the comparison
    # view. Only whitespace removal on already-NFC text has a direct index map;
    # mathematical rewrites or Unicode composition cannot safely supply one.
    if unicodedata.normalize('NFC', original) == original and _document_text(original) == expected:
        from xml.etree.ElementTree import Element
        from .xml_translator.xml import encode_friendly
        positions = [index for index, char in enumerate(original) if not is_whitespace(char)]
        for kind, start, end, _, _ in edits[:10]:
            if kind not in ('delete', 'replace') or start == end:
                continue
            raw = original[positions[start]:positions[end-1]+1]
            excerpt = Element('excerpt', {'xml:space': 'preserve'})
            excerpt.text = raw[:6000]
            details += '\n' + _FEEDBACK.get_template('original_missing_excerpt.jinja').render(
                start=start, end=end, excerpt=encode_friendly(excerpt), truncated=len(raw) > 6000).strip()
    if any(expected[a:b] and expected[a:b] in actual
           for kind, a, b, _, _ in edits if kind in ('delete', 'replace')):
        details += '\n' + _FEEDBACK.get_template('repeated_missing.jinja').render().strip()
    message = f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
    def fields(node, path):
        yield path + '.text', node.text or ''
        for index, child in enumerate(node):
            child_path = f'{path}/{child.tag}[{index}]'
            yield from fields(child, child_path)
            yield child_path + '.tail', child.tail or ''

    field_blocks = []
    for block in blocks:
        path = f'{block.tag}#{block.get("id")}'
        values = [*fields(block, path), (path + '.tail', block.tail or '')]
        field_blocks.append((block, [(path, value, _document_text(value)) for path, value in values]))
    if ''.join(normalized for _, values in field_blocks for _, _, normalized in values) != actual:
        field_blocks = [(block, [(path, value, comparison_text(value)) for path, value, _ in values])
                        for block, values in field_blocks]
    if ''.join(normalized for _, values in field_blocks for _, _, normalized in values) != actual:
        # Only report offsets when the complete comparison view is provably
        # decomposable into these field views. Cross-field math may not be.
        return message
    cursor, locations = 0, []
    for block, values in field_blocks:
        end = cursor + sum(len(normalized) for _, _, normalized in values)
        if any((cursor <= start < end) if start == finish else (cursor < finish and start < end)
               for _, _, _, start, finish in edits):
            whole_extra = end > cursor and any(kind == 'insert' and start <= cursor and end <= finish
                                               for kind, _, _, start, finish in edits)
            locations.append(f'block id={block.get("id")}: candidate[{cursor}:{end}], '
                             f'entire_block_extra={whole_extra}, text={"".join(block.itertext())[:200]!r}')
        cursor = end
    cursor = 0
    located_missing = set()
    field_locations = []
    for _, values in field_blocks:
        for path, value, normalized in values:
            start, end = cursor, cursor + len(normalized)
            cursor = end
            if end <= start:
                continue
            for index, (kind, a, b, c, d) in enumerate(edits):
                if kind == 'delete' and c == d and start <= c <= end and index not in located_missing:
                    boundary = 'BEGINNING' if c == start else 'END' if c == end else None
                    if boundary is not None:
                        field_locations.append(_FEEDBACK.get_template('missing_location.jinja').render(
                            path=path, boundary=boundary, missing=repr(expected[a:b][:6000]),
                            current=repr(value)).strip())
                        located_missing.add(index)
            if any((start <= left < end) if left == right else (start < right and left < end)
                   for _, _, _, left, right in edits):
                extra = any(kind == 'insert' and left <= start and end <= right
                            for kind, _, _, left, right in edits)
                field_locations.append(f'field={path}, candidate[{start}:{end}], '
                                 f'entire_field_extra={extra}, value={value[:300]!r}')
    if locations or field_locations:
        details += '\n' + '\n'.join([*locations[:12], *field_locations[:12]])
    return f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
