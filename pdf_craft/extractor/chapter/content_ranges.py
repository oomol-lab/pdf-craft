"""Literal character ranges over OCR content, independent of its wrappers."""
from dataclasses import replace

from .analysis_types import _AnalysedContent, _AnalysedContentPart, _AnalysedHTMLTag, _ReferencePlaceholder, _AnalysedInlineExpression


def content_text(content: _AnalysedContent) -> str:
    return ''.join(part if isinstance(part, str) else
                   content_text(part.children) if isinstance(part, _AnalysedHTMLTag) else
                   part.text if isinstance(part, _ReferencePlaceholder) else part.content
                   for part in content)


def slice_content(content: _AnalysedContent, start: int, end: int, *, include_end: bool = True) -> _AnalysedContent:
    """Copy a literal range, splitting formatting and formula wrappers as needed.

    No parser reinterprets the selected characters. Empty structural nodes belong
    to the range on their right, or to the final range at the source's end.
    """
    total = len(content_text(content))
    include_end = include_end and end == total
    if start == 0 and end == total and include_end:
        return content
    result: list[_AnalysedContentPart] = []
    offset = 0
    for part in content:
        text = content_text((part,))
        length = len(text)
        lo, hi = max(start, offset), min(end, offset + length)
        if length == 0:
            if start <= offset and (offset < end or include_end and offset == end):
                result.append(part)
        elif isinstance(part, _AnalysedHTMLTag) and lo <= hi:
            children = slice_content(part.children, lo-offset, hi-offset,
                                     include_end=hi < end or include_end)
            if children:
                result.append(replace(part, children=children))
        elif lo < hi:
            if lo == offset and hi == offset + length:
                result.append(part)
            elif isinstance(part, str):
                result.append(part[lo-offset:hi-offset])
            elif isinstance(part, _ReferencePlaceholder):
                result.append(text[lo-offset:hi-offset])
            elif isinstance(part, _AnalysedInlineExpression):
                result.append(replace(part, content=part.content[lo-offset:hi-offset]))
        offset += length
    return tuple(result)


def replace_ranges(content: _AnalysedContent,
                   replacements: list[tuple[int, int, _AnalysedContentPart]]) -> _AnalysedContent:
    """Replace disjoint ordered ranges without consuming neighbouring characters.

    A replacement may cross arbitrary source wrappers. Unselected prefix/suffix
    wrappers are copied; the replacement lives outside those split wrappers.
    """
    total, cursor = len(content_text(content)), 0
    result: list[_AnalysedContentPart] = []
    for start, end, replacement in replacements:
        if start > cursor:
            result.extend(slice_content(content, cursor, start, include_end=False))
        result.append(replacement)
        cursor = end
    result.extend(slice_content(content, cursor, total))
    return tuple(result)
