"""Lossless compact text evidence for local JEV questions, never stored OCR.

Sparse second-pass images can produce thousands of repeated tokens. Represent
literal runs rather than dropping them or allowing them to consume the entire
review context. This encoding makes no claim that a repeated passage is wrong.
"""
from copy import deepcopy
from itertools import groupby
import re
from typing import Any

_REPEAT = re.compile(r'(.{1,64}?)\1{15,}', re.S)
_SEQUENCE = re.compile(r'(?:[0-9]+\.[ \n]*){32,}')
_NUMBER = re.compile(r'([0-9]+)(\.[ \n]*)')

TEXT_ENCODING_NOTE = (
    'A raw_text object with literal_parts encodes exact OCR text. Concatenate its '
    'parts: strings are literal; {repeat: text, count: N} repeats that exact text N '
    'times; {integer_sequence: {start, count, suffix}} emits count successive '
    'integers starting at start, each followed by the exact suffix. This is '
    'lossless encoding, not a correction or an instruction printed on the page. '
    'Repetition alone does not prove a structural error.'
)


def _integer_parts(text: str) -> list[Any]:
    parts: list[Any] = []
    end = 0
    for match in _SEQUENCE.finditer(text):
        items = list(_NUMBER.finditer(match[0]))
        for _, grouped in groupby(enumerate(items),
                                  lambda pair: (int(pair[1][1]) - pair[0], pair[1][2])):
            group = [item for _, item in grouped]
            start, suffix = int(group[0][1]), group[0][2]
            if len(group) < 32 or any(m[1] != str(start + i) for i, m in enumerate(group)):
                continue
            first, last = match.start() + group[0].start(), match.start() + group[-1].end()
            if first > end:
                parts.append(text[end:first])
            parts.append({'integer_sequence': {'start': start, 'count': len(group), 'suffix': suffix}})
            end = last
    if end < len(text):
        parts.append(text[end:])
    return parts


def encode_review_text(text: str) -> str | dict[str, Any]:
    """Encode literal/ordinal runs without changing any source character."""
    if len(text) < 1000:
        return text
    parts: list[Any] = []
    for part in _integer_parts(text):
        if not isinstance(part, str):
            parts.append(part)
            continue
        end = 0
        for match in _REPEAT.finditer(part):
            if match.start() > end:
                parts.append(part[end:match.start()])
            parts.append({'repeat': match[1], 'count': len(match[0]) // len(match[1])})
            end = match.end()
        if end < len(part):
            parts.append(part[end:])
    return {'literal_parts': parts} if any(not isinstance(p, str) for p in parts) else text


def compact_review_evidence(value: Any) -> Any:
    """Copy raw evidence, retaining annotated text only for active references.

    Reference locations need both the original mark and its analysed placement.
    Other analysed text is a duplicate normalization, not independent evidence.
    IDs, field names, ownership hints and ordering remain ordinary JSON values.
    """
    if isinstance(value, list):
        return [compact_review_evidence(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key == 'analysed_text' and not value.get('references') and '⟦REF⟧' not in str(item):
            continue
        result[key] = (encode_review_text(item)
                       if key in ('raw_text', 'analysed_text') and isinstance(item, str)
                       else compact_review_evidence(item))
    return result
