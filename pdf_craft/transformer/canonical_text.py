"""Recover known canonical whitespace only when its slot ownership is unique."""
from collections import defaultdict
from typing import NamedTuple
from xml.etree.ElementTree import Element

from .whitespace import DOCUMENT_SPACE, WhiteSpace, collapses_breaks, mode_for, normalize


class _Field(NamedTuple):
    element: Element
    attribute: str
    path: list[Element]
    mode: WhiteSpace
    source: str
    text: str
    start: int
    end: int


def _gaps(text):
    offset, gaps = 0, defaultdict(str)
    for char in text:
        if char in DOCUMENT_SPACE:
            gaps[offset] += char
        else:
            offset += 1
    return gaps


def restore_canonical_whitespace(roots: list[Element], canonical: str,
                                 sources: list[Element] | None = None,
                                 modes: list[WhiteSpace] | None = None) -> bool:
    """Align unchanged characters, then reconstruct legal gaps transactionally.

    Text, punctuation, NBSP and other Unicode separators remain immutable.
    Missing gaps inside one field are unique. At a tag/fragment boundary use
    the existing gap owner or a unique source owner; formula padding belongs
    outside the expression that the postprocessor will replace. Otherwise retry.
    """
    fields: list[_Field] = []
    offset = 0

    def field(element, attribute, path, mode, source):
        nonlocal offset
        text = normalize(getattr(element, attribute) or '', mode)
        end = offset + sum(char not in DOCUMENT_SPACE for char in text)
        fields.append(_Field(element, attribute, path, mode, source or '', text, offset, end))
        offset = end

    def walk(element, source, inherited, path):
        mode = mode_for(source if source is not None else element, inherited)
        field(element, 'text', path, mode, source.text if source is not None else '')
        children = list(source) if source is not None else []
        ids = {child.get('id'): child for child in children if child.get('id') is not None}
        for index, child in enumerate(element):
            original = ids.get(child.get('id')) if child.get('id') is not None else (
                children[index] if index < len(children) and children[index].tag == child.tag else None)
            walk(child, original, mode, path + [child])
            field(child, 'tail', path, mode, original.tail if original is not None else '')

    for index, root in enumerate(roots):
        walk(root, sources[index] if sources is not None else None,
             modes[index] if modes is not None else WhiteSpace.NORMAL, [root])
    actual = ''.join(item.text for item in fields)
    canonical = canonical.replace('\r\n', '\n').replace('\r', '\n')
    if ''.join(c for c in actual if c not in DOCUMENT_SPACE) != ''.join(
            c for c in canonical if c not in DOCUMENT_SPACE):
        return False
    actual_gaps, expected_gaps = _gaps(actual), _gaps(canonical)
    changes: dict[int, str] = {}
    wanted: dict[int, str] = {}
    for position in actual_gaps.keys() | expected_gaps.keys():
        protected_modes = {f.mode for f in fields if not collapses_breaks(f.mode) and f.start <= position <= f.end}
        mode = next(iter(protected_modes)) if len(protected_modes) == 1 else WhiteSpace.PRE
        protected = bool(protected_modes)
        expected = normalize(expected_gaps[position], mode) if protected else normalize(expected_gaps[position])
        if not protected and position in (0, offset):
            expected = ''  # Normal paragraph edges, not independently trimmed XML nodes.
        if expected:
            wanted[position] = expected
        if actual_gaps[position] != expected:
            if protected and normalize(actual_gaps[position], mode) != expected:
                return False
            changes[position] = expected
    values = [f.text for f in fields]
    for position, expected in sorted(changes.items()):
        candidates = [i for i, f in enumerate(fields) if f.start <= position <= f.end]
        target = None
        if expected:
            existing = [i for i in candidates if position - fields[i].start in _gaps(fields[i].text)]
            interior = [i for i in candidates if fields[i].start < position < fields[i].end]
            owners = [i for i in candidates if (
                (position == fields[i].start and fields[i].source[:1] in DOCUMENT_SPACE and fields[i].source)
                or (position == fields[i].end and fields[i].source[-1:] in DOCUMENT_SPACE and fields[i].source))]
            if existing:
                target = existing[0]  # CSS collapses a run across inline boundaries.
            elif len(interior) == 1:
                target = interior[0]
            elif len(owners) == 1:
                target = owners[0]
            else:
                # Source formula identities can be restored from empty slots;
                # separators must live outside their replaceable contents.
                expressions = [f for f in fields if f.element.tag == 'expression' and f.attribute == 'text'
                               and position in (f.start, f.end)]
                if expressions:
                    outside = [i for i in candidates if all(n.tag != 'expression' for n in fields[i].path)]
                    if outside:
                        target = outside[0]
            if target is None:
                return False
        for i in candidates:
            f = fields[i]
            result, cursor = [], f.start
            inserted = False
            for char in values[i]:
                if cursor == position and not inserted:
                    if i == target:
                        result.append(expected)
                    inserted = True
                if char not in DOCUMENT_SPACE or cursor != position:
                    result.append(char)
                if char not in DOCUMENT_SPACE:
                    cursor += 1
            if cursor == position and not inserted and i == target:
                result.append(expected)
            values[i] = ''.join(result)
    if dict(_gaps(''.join(values))) != wanted:
        return False
    # All recovery succeeded before changing any response fields.
    for f, value in zip(fields, values):
        setattr(f.element, f.attribute, value or None)
    return True
