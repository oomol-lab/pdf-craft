"""Lossless compact text evidence for local JEV questions, never stored OCR.

Sparse second-pass images can produce thousands of repeated tokens. Represent
literal runs rather than dropping them or allowing them to consume the entire
review context. This encoding makes no claim that a repeated passage is wrong.
"""
from copy import deepcopy
from difflib import SequenceMatcher
from itertools import groupby
import re
from typing import Any
from .review_templates import review_template

_REPEAT = re.compile(r'(.{1,64}?)\1{15,}', re.S)
_SEQUENCE = re.compile(r'(?:[0-9]+\.[ \n]*){32,}')
_NUMBER = re.compile(r'([0-9]+)(\.[ \n]*)')

TEXT_ENCODING_NOTE = (
    review_template('shared_review', 'text_encoding_note')
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


def literal_reference_edits(raw: str, analysed: str) -> list[dict[str, str]] | None:
    """Summarize exact edits, or omit an oversized comparison without a verdict."""
    if raw == analysed:
        return []
    prefix = 0
    while prefix < min(len(raw), len(analysed)) and raw[prefix] == analysed[prefix]:
        prefix += 1
    end_raw, end_analysed = len(raw), len(analysed)
    while (
        end_raw > prefix
        and end_analysed > prefix
        and raw[end_raw - 1] == analysed[end_analysed - 1]
    ):
        end_raw -= 1
        end_analysed -= 1
    if max(end_raw - prefix, end_analysed - prefix) > 4096:
        return None
    return [
        dict(
            original=raw[prefix + a : prefix + b],
            analysed=analysed[prefix + c : prefix + d],
            original_before=raw[max(0, prefix + a - 16) : prefix + a],
            original_after=raw[prefix + b : prefix + b + 16],
        )
        for op, a, b, c, d in SequenceMatcher(
            None, raw[prefix:end_raw], analysed[prefix:end_analysed], autojunk=False
        ).get_opcodes()
        if op != "equal"
    ]
