"""Lossless engineering partitions inside an artist mathematical occurrence."""
import re
from dataclasses import replace
from typing import cast

from ..formula.plain_text import try_latex_to_plain_text
from .whitespace import normalize

from .canonical_projection import SourceMath, MathProse, formula_content


def _literal_percent(text: str) -> str:
    # Plain translated percentages are literal text, not TeX comments.
    return re.sub(r'(?<!\\)%', r'\\%', text)


def _unchanged(source: str, translated: str) -> bool:
    # A flattened percentage sign is literal, not a TeX comment. Do not erase
    # internal whitespace or punctuation to manufacture a successful match.
    old = try_latex_to_plain_text(_literal_percent(source))
    new = try_latex_to_plain_text(_literal_percent(translated))
    return (old is not None and new is not None and bool(normalize(old, trim=True))
            and normalize(old, trim=True) == normalize(new, trim=True))


def _unchanged_group(sources: list[SourceMath], translated: str) -> bool:
    contents = [formula_content(source.latex)[0] for source in sources]
    # A separator at an explicit boundary between source identities is not
    # whitespace inside either formula. Retain the existing merged-node path.
    return any(_unchanged(separator.join(contents), translated) for separator in ('', ' '))


def partition_math(original: str, sources: list[SourceMath], parts: object, *, residual_math: bool = True) -> list[SourceMath | str | MathProse]:
    """Check span ownership, then restore source notation only for equal flattened text.

    Comparison never selects identities or vetoes a changed translated value.
    """
    content, inline = formula_content(original)
    if parts is None:
        if len(sources) > 1 and not _unchanged_group(sources, content):
            raise ValueError('Supply explicit parts for multiple source identities so their translated boundaries are known.')
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
            selected = [sources[index] for index in indexes]
            if len(selected) > 1:
                if not _unchanged_group(selected, part['text']):
                    raise ValueError('Partition multiple source identities into individual translated parts.')
                bound.extend(selected)
            else:
                source = selected[0]
                source_text = formula_content(source.latex)[0]
                unchanged = _unchanged(source_text, part['text'])
                opening, closing = ('\\(', '\\)') if inline else ('\\[', '\\]')
                bound.append(source if unchanged else replace(
                    source, translated_latex=opening + _literal_percent(part['text']) + closing))
        else:
            # Preserve artist math as ordinary canonical text, with mathematical
            # delimiters retained for downstream renderers; invent no rich node.
            opening, closing = ('\\(', '\\)') if inline else ('\\[', '\\]')
            bound.append((opening + part['text'] + closing if part['text'].strip(' \t\r\n') else part['text'])
                         if residual_math else MathProse(part['text']))
    if sorted(seen) != list(range(len(sources))):
        raise ValueError('Each selected source identity must occur exactly once in mathematical parts.')
    return bound
