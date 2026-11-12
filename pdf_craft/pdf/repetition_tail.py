"""Bounded, literal OCR-tail recovery before source positions are captured.

This does not judge meaning or deduplicate a document. Only long, contiguous
periodic suffixes are candidates; the first complete unit is retained. The
comparison surface ignores shared Unicode whitespace, but every cut addresses
an original character boundary. No model or accepted chapter is involved.
"""
from dataclasses import dataclass, replace
import logging
from typing import TYPE_CHECKING

from ..common.whitespace import is_whitespace

if TYPE_CHECKING:
    from .types import Page, PageLayout

_LOG = logging.getLogger(__name__)
_MAX_PERIOD = 256
_MIN_REPEATS = 16
_MIN_REDUNDANT = 256


@dataclass(frozen=True)
class TailCut:
    """Diagnostic evidence; positions address the unchanged input string."""
    start: int
    end: int
    period: int
    repetitions: int
    sample: str


def _prefix_matches(text: str) -> list[int]:
    """Z array: all shifted prefix matches in linear time and space."""
    matches = [0] * len(text)
    left = right = 0
    for index in range(1, len(text)):
        if index <= right:
            matches[index] = min(right - index + 1, matches[index-left])
        while index + matches[index] < len(text) and text[matches[index]] == text[index+matches[index]]:
            matches[index] += 1
        if index + matches[index] - 1 > right:
            left, right = index, index + matches[index] - 1
    return matches


def _closed_cut(text: str, cut: int, limit: int) -> int | None:
    """Do not retain a partial TeX group/wrapper at a rotated repeat boundary.

    This is delimiter accounting, not mathematical interpretation. Uncertain
    nesting remains untouched; no parser repairs or regenerates source text.
    """
    stack = []
    index = 0
    while index < limit:
        if index >= cut and not stack:
            while index < limit and text[index] in '.,;:!?':
                index += 1
            return index
        char = text[index]
        if char == chr(92) and index + 1 < limit:
            other = text[index+1]
            if other in '([':
                stack.append(chr(92) + (')' if other == '(' else ']'))
                index += 2
                continue
            if other in ')]':
                if not stack or stack.pop() != chr(92) + other:
                    return None
                index += 2
                continue
            if other in '{}$' or other == chr(92):
                index += 2
                continue
        if char == '{':
            stack.append('}')
        elif char == '}':
            if not stack or stack.pop() != '}':
                return None
        elif char == '$':
            marker = '$$' if text.startswith('$$', index) else '$'
            if stack and stack[-1] == marker:
                stack.pop()
            else:
                stack.append(marker)
            index += len(marker)
            continue
        index += 1
    return None


def repeated_tail(text: str) -> TailCut | None:
    """Find a long periodic suffix, including an unfinished final repetition.

    Pure numeric/punctuation padding, HTML/table markup and fenced code are not
    evidence. These conservative exclusions deliberately leave uncertain input
    alone. Period bounds cap candidate work independently of repeat count.
    """
    if len(text) < _MIN_REDUNDANT or '<' in text or '```' in text or '~~~' in text:
        return None
    positions = [index for index, char in enumerate(text) if not is_whitespace(char)]
    view = ''.join(text[index] for index in positions)
    if len(view) < _MIN_REDUNDANT:
        return None
    matches = _prefix_matches(view[::-1])
    for period in range(2, min(_MAX_PERIOD, len(view) // _MIN_REPEATS) + 1):
        length = period + matches[period]
        if length < period * _MIN_REPEATS or length - period < _MIN_REDUNDANT:
            continue
        start = len(view) - length
        unit = view[start:start+period]
        if not any(char.isalpha() for char in unit) or len(set(unit)) < 2:
            continue
        # Preserve the whole first occurrence and its actual spelling/spacing.
        # The final partial occurrence is discarded with the redundant suffix.
        cut = _closed_cut(text, positions[start+period-1] + 1,
                          positions[min(start+2*period, len(positions)-1)] + 1)
        if cut is None:
            return None
        return TailCut(cut, len(text), period, length // period, text[positions[start]:cut][:128])
    return None


def _eligible(layout: 'PageLayout') -> bool:
    return layout.ref in ('text', 'sub_title', 'image_caption', 'table_caption', 'equation_caption', 'equation', 'formula')


def clean_layouts(layouts: list['PageLayout']) -> tuple[list['PageLayout'], list[dict]]:
    """Copy changed text only; empty observations retain identity and geometry."""
    output = list(layouts)
    reports = []
    for index, layout in enumerate(output):
        cut = repeated_tail(layout.text) if _eligible(layout) else None
        if cut is not None:
            output[index] = replace(layout, text=layout.text[:cut.start])
            reports.append(dict(layout_order=layout.order, start=cut.start, end=cut.end,
                                period=cut.period, repetitions=cut.repetitions, sample=cut.sample))
    # A vendor may emit one short block per repeated occurrence. Examine only
    # the final uninterrupted textual run, never bridge a table/image or a page.
    start = len(output)
    while start and _eligible(output[start-1]):
        start -= 1
    tail = output[start:]
    if len(tail) > 1:
        joined = '\n'.join(layout.text for layout in tail)
        cut = repeated_tail(joined)
        if cut is not None:
            offset = 0
            for index in range(start, len(output)):
                layout = output[index]
                keep = max(0, min(len(layout.text), cut.start-offset))
                if keep < len(layout.text):
                    output[index] = replace(layout, text=layout.text[:keep])
                    reports.append(dict(layout_order=layout.order, start=keep, end=len(layout.text),
                                        period=cut.period, repetitions=cut.repetitions, sample=cut.sample))
                offset += len(layout.text) + 1
    return output, reports


def clean_page(page: 'Page') -> 'Page':
    """Materialize current OCR input, including old cache reads, without edits to raw evidence."""
    body, body_cuts = clean_layouts(page.body_layouts)
    notes, note_cuts = clean_layouts(page.footnotes_layouts)
    for stream, cuts in (('body', body_cuts), ('footnotes', note_cuts)):
        for cut in cuts:
            _LOG.warning('OCR repeated tail removed: page=%s stream=%s layout=%s range=%s:%s period=%s repeats=%s',
                         page.index, stream, cut['layout_order'], cut['start'], cut['end'],
                         cut['period'], cut['repetitions'], extra={'ocr_tail_cut': cut})
    return replace(page, body_layouts=body, footnotes_layouts=notes) if body_cuts or note_cuts else page
