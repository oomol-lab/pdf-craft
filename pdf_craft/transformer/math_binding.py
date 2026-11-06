"""Lossless engineering partitions inside an artist mathematical occurrence."""
import unicodedata
from typing import cast

from .canonical_projection import SourceMath, formula_content


def _compact(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text) if c not in ' \t\r\n')


def partition_math(original: str, sources: list[SourceMath], parts: object, *, require_parts: bool = True) -> list[SourceMath | str]:
    """Check explicit span ownership after semantic source IDs are chosen.

    This never chooses an identity by string similarity. A literal containment
    check only detects an oversized replacement that would erase a residual.
    """
    content, inline = formula_content(original)
    if parts is None:
        source_text = ''.join(formula_content(source.latex)[0] for source in sources)
        if require_parts and _compact(content) != _compact(source_text):
            raise ValueError(f'Mathematical occurrence {content!r} differs from source-node contents {source_text!r}. '
                             'Supply explicit parts to distinguish source-owned spans from residual artist text; '
                             'retain all content outside those spans.')
        parts = [dict(text=content, keys=list(range(len(sources))))]
    if not isinstance(parts, list) or not parts:
        raise ValueError('Math parts must be a nonempty complete partition.')
    if any(not isinstance(part, dict) or not isinstance(part.get('text'), str) or not part['text']
           for part in parts):
        raise ValueError('Every mathematical part needs nonempty exact text.')
    parts = cast(list[dict], parts)
    if ''.join(part['text'] for part in parts) != content:
        raise ValueError(f'Math parts must concatenate to the complete unchanged delimiter contents: {content!r}.')
    seen, bound = [], []
    for part in parts:
        indexes = part.get('keys', [])
        if (not isinstance(indexes, list) or any(type(index) is not int or not 0 <= index < len(sources) for index in indexes)):
            raise ValueError('Invalid source identities in mathematical parts.')
        seen.extend(indexes)
        if indexes:
            if len(indexes) > 1 and _compact(part['text']) != ''.join(
                    _compact(formula_content(sources[index].latex)[0]) for index in indexes):
                raise ValueError('Partition a changed combined occurrence into individual source-owned and residual parts.')
            selected = [sources[index] for index in indexes]
            source_text = ''.join(formula_content(source.latex)[0] for source in selected)
            small, large = _compact(source_text), _compact(part['text'])
            if small and small != large and small in large:
                raise ValueError(f'Replacement span {part["text"]!r} contains source content {source_text!r} plus residual characters. '
                                 'Use parts to bind only its source-owned span and preserve the remaining text as parts without keys.')
            bound.extend(selected)
        else:
            # Preserve artist math as ordinary canonical text, with mathematical
            # delimiters retained for downstream renderers; invent no rich node.
            opening, closing = ('\\(', '\\)') if inline else ('\\[', '\\]')
            bound.append(opening + part['text'] + closing)
    if sorted(seen) != list(range(len(sources))):
        raise ValueError('Each selected source identity must occur exactly once in mathematical parts.')
    return bound
