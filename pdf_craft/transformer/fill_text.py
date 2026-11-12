"""Plain artist view and symmetric comparison, independent of XML field layout."""
from difflib import SequenceMatcher
import re
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


def _raw_excerpt(original: str, view: str, start: int, end: int, tag: str, limit: int = 600):
    # Only NFC plus whitespace deletion supplies a reliable inverse index map.
    # Never manufacture a model field from a mathematical comparison view.
    if (start == end or unicodedata.normalize('NFC', original) != original
            or _document_text(original) != view):
        return None
    from xml.etree.ElementTree import Element
    from .xml_translator.xml import encode_friendly
    positions = [index for index, char in enumerate(original) if not is_whitespace(char)]
    raw = original[positions[start]:positions[end-1]+1]
    excerpt = Element(tag, {'xml:space': 'preserve'})
    excerpt.text = raw[:limit]
    return encode_friendly(excerpt), len(raw) > limit


def _literal_occurrences(text: str, literal: str) -> int:
    """Count overlapping literal matches, never infer a repeated semantic unit."""
    count, offset = 0, 0
    while literal and (offset := text.find(literal, offset)) >= 0:
        count += 1
        offset += 1
    return count


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
    original, original_actual = expected, actual
    # Optional mathematical interpretation cannot invalidate existing text equivalence.
    if _document_text(expected) == _document_text(actual):
        return None
    expected, actual = comparison_text(expected), comparison_text(actual)
    if expected == actual:
        return None
    edits = _comparison_edits(expected, actual)
    missing = sum(end-start for _, start, end, _, _ in edits)
    added = sum(end-start for _, _, _, start, end in edits)
    names = {'delete': 'missing', 'insert': 'extra', 'replace': 'different'}
    details = '\n'.join(f'{names[kind]}: expected[{a}:{b}]={expected[a:b][:600]!r}; '
                        f'candidate[{c}:{d}]={actual[c:d][:600]!r}; '
                        f'expected context={(expected[max(0,a-50):a], expected[b:b+50])!r}; '
                        f'candidate context={(actual[max(0,c-50):c], actual[d:d+50])!r}'
                        for kind, a, b, c, d in edits[:10])
    for kind, a, b, c, d in edits[:10]:
        if kind in ('delete', 'replace'):
            excerpt = _raw_excerpt(original, expected, a, b, 'excerpt', limit=6000)
            if excerpt is not None:
                value, truncated = excerpt
                details += '\n' + _FEEDBACK.get_template('original_missing_excerpt.jinja').render(
                    start=a, end=b, excerpt=value, truncated=truncated, limit=6000).strip()
        if kind in ('insert', 'replace'):
            excerpt = _raw_excerpt(original_actual, actual, c, d, 'extra_excerpt')
            if excerpt is not None:
                value, truncated = excerpt
                details += '\n' + _FEEDBACK.get_template('original_extra_excerpt.jinja').render(
                    start=c, end=d, excerpt=value, truncated=truncated).strip()
    # Bounded excerpts of the actual diff, not a run-length encoding or an
    # allocation plan. Both complete texts remain in the request and the gate.
    statistics = []
    samples = set()
    for _, a, b, c, d in edits[:10]:
        for excerpt in (expected[a:b][:64], actual[c:d][:64]):
            # Short literal words are easier to check than a sample spanning
            # several repetitions. This is lexical evidence, not a formula or
            # sentence parser, a minimal-period search or a compressed payload.
            words = sorted(set(re.findall(r'[^\W\d_]{2,}', excerpt)), key=lambda word: (-len(word), word))[:2]
            for sample in (excerpt, *words):
                if not sample or sample in samples:
                    continue
                samples.add(sample)
                expected_count = _literal_occurrences(expected, sample)
                actual_count = _literal_occurrences(actual, sample)
                if expected_count and actual_count and max(expected_count, actual_count) > 1:
                    from xml.etree.ElementTree import Element
                    from .xml_translator.xml import encode_friendly
                    literal = Element('literal', {'xml:space': 'preserve'})
                    literal.text = sample
                    statistics.append(dict(sample=sample, literal=encode_friendly(literal), expected=expected_count, actual=actual_count))
    if statistics:
        details += '\n' + _FEEDBACK.get_template('literal_occurrences.jinja').render(
            statistics=statistics[:4]).strip()
    if any(expected[a:b] and expected[a:b] in actual
           for kind, a, b, _, _ in edits if kind in ('delete', 'replace')):
        details += '\n' + _FEEDBACK.get_template('repeated_missing.jinja').render().strip()
    message = f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
    def fields(node, path):
        yield path + '.text', node.text or ''
        for index, child in enumerate(node):
            identity = f'#{child.get("id")}' if child.get('id') is not None else f'[{index}]'
            child_path = f'{path}/{child.tag}{identity}'
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
    wholly_extra_fields = []
    localized_counts = []
    for _, values in field_blocks:
        for path, value, normalized in values:
            start, end = cursor, cursor + len(normalized)
            cursor = end
            if end <= start:
                continue
            for index, (kind, a, b, c, d) in enumerate(edits):
                if kind in ('insert', 'replace') and c < end and start < d:
                    left, right = max(start, c) - start, min(end, d) - start
                    raw_left = raw_right = None
                    if unicodedata.normalize('NFC', value) == value and _document_text(value) == normalized:
                        positions = [i for i, char in enumerate(value) if not is_whitespace(char)]
                        raw_left = positions[left]
                        raw_right = positions[right] if right < len(positions) else len(value)
                    field_locations.append(_FEEDBACK.get_template('extra_field_location.jinja').render(
                        path=path, left=left, right=right, raw_left=raw_left, raw_right=raw_right).strip())
                if kind == 'delete' and c == d and start <= c <= end and index not in located_missing:
                    boundary = 'BEGINNING' if c == start else 'END' if c == end else None
                    if boundary is not None:
                        field_locations.append(_FEEDBACK.get_template('missing_location.jinja').render(
                            path=path, boundary=boundary, missing=repr(expected[a:b][:600]),
                            current=repr(value[:300])).strip())
                        located_missing.add(index)
                    elif index not in located_missing:
                        offset = c - start
                        raw_offset = None
                        if unicodedata.normalize('NFC', value) == value and _document_text(value) == normalized:
                            positions = [i for i, char in enumerate(value) if not is_whitespace(char)]
                            raw_offset = positions[offset]
                        field_locations.append(_FEEDBACK.get_template('interior_missing_location.jinja').render(
                            path=path, offset=offset, raw_offset=raw_offset).strip())
                        located_missing.add(index)
            if not localized_counts and all(start <= c <= d <= end for _, _, _, c, d in edits):
                for statistic in statistics[:4]:
                    sample = statistic['sample']
                    # Only offer local arithmetic when matches do not straddle
                    # XML fields. This is a conditional diagnostic, not a quota
                    # or a generated replacement field.
                    contained = sum(_literal_occurrences(view, sample)
                                    for _, fields_view in field_blocks for _, _, view in fields_view)
                    if contained != statistic['actual']:
                        continue
                    field_count = _literal_occurrences(normalized, sample)
                    outside = statistic['actual'] - field_count
                    target = statistic['expected'] - outside
                    if target >= 0:
                        localized_counts.append(dict(path=path, literal=statistic['literal'],
                            actual=field_count, outside=outside, target=target))
            if any((start <= left < end) if left == right else (start < right and left < end)
                   for _, _, _, left, right in edits):
                extra = any(kind == 'insert' and left <= start and end <= right
                            for kind, _, _, left, right in edits)
                if extra:
                    wholly_extra_fields.append(path)
                field_locations.append(f'field={path}, candidate[{start}:{end}], '
                                 f'entire_field_extra={extra}, value={value[:300]!r}')
    if localized_counts:
        details += '\n' + _FEEDBACK.get_template('field_literal_occurrences.jinja').render(
            statistics=localized_counts).strip()
    if wholly_extra_fields:
        details += '\n' + _FEEDBACK.get_template('extra_fields.jinja').render(
            fields=wholly_extra_fields[:256], total=len(wholly_extra_fields)).strip()
    if locations or field_locations:
        details += '\n' + '\n'.join([*locations[:12], *field_locations[:12]])
    return f'Visible text differs from the canonical translation: Missing {missing}, added {added} characters.\n{details}'
