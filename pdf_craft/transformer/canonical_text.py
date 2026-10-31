"""Recover known canonical whitespace only when its slot ownership is unique."""
from collections import defaultdict
import unicodedata
from typing import NamedTuple
from xml.etree.ElementTree import Element

from .whitespace import DOCUMENT_SPACE, WhiteSpace, collapses_breaks, mode_for, normalize
from .xml_translator.xml.matching import element_id, match_children


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


def _decompose_codepoints(text: str) -> str:
    # Keep each character's marks in its original position until gaps have
    # been restored. Whole-string NFD could reorder them across a missing gap.
    return ''.join(unicodedata.normalize('NFD', char) for char in text)


def _source_owners(fields, position):
    return [f for f in fields if f.source and (
        (position == f.start and f.source[:1] in DOCUMENT_SPACE)
        or (position == f.end and f.source[-1:] in DOCUMENT_SPACE))]


def restore_canonical_whitespace(roots: list[Element], canonical: str,
                                 sources: list[Element] | None = None,
                                 modes: list[WhiteSpace] | None = None, *,
                                 replaced_tags: frozenset[str] = frozenset()) -> bool:
    """Align unchanged characters, then reconstruct legal gaps transactionally.

    Text, punctuation, NBSP and other Unicode separators remain immutable.
    Missing gaps inside one field are unique. At a tag/fragment boundary use
    the existing gap owner or a unique source owner. At boundaries of adapter-
    declared replacement nodes, only surviving fields can own the separator,
    even when the candidate's concatenated text already matches. Otherwise retry.
    """
    fields: list[_Field] = []
    offset = 0
    replacement_boundaries: set[int] = set()

    def field(element, attribute, path, mode, source):
        nonlocal offset
        text = normalize(getattr(element, attribute) or '', mode)
        end = offset + sum(char not in DOCUMENT_SPACE for char in text)
        fields.append(_Field(element, attribute, path, mode, source or '', text, offset, end))
        offset = end

    def walk(element, source, inherited, path):
        start = offset
        mode = mode_for(source if source is not None else element, inherited)
        field(element, 'text', path, mode, source.text if source is not None else '')
        originals = {} if source is None else {
            id(child): original for original, child in match_children(
                ((node.tag, element_id(node), node) for node in source), element,
            )
        }
        for child in element:
            original = originals.get(id(child))
            # A provided source is authoritative. A missing match must never
            # let model-supplied attributes override its preservation context.
            if source is not None and original is None:
                return False
            if not walk(child, original, mode, path + [child]):
                return False
            field(child, 'tail', path, mode, original.tail if original is not None else '')
        if element.tag in replaced_tags:
            replacement_boundaries.update((start, offset))
        return True

    if sources is not None and len(sources) != len(roots):
        return False
    for index, root in enumerate(roots):
        if not walk(root, sources[index] if sources is not None else None,
                    modes[index] if modes is not None else WhiteSpace.NORMAL, [root]):
            return False
    actual = ''.join(item.text for item in fields)
    canonical = canonical.replace('\r\n', '\n').replace('\r', '\n')
    visible = lambda text: ''.join(c for c in text if c not in DOCUMENT_SPACE)
    if visible(actual) != visible(canonical):
        # Decompose without reordering source characters: whitespace may be
        # missing between marks, and that boundary must be restored first.
        # Normalized comparison keeps existing whitespace as a hard barrier.
        canonical = unicodedata.normalize('NFD', canonical)
        if (visible(_decompose_codepoints(actual)) != visible(canonical)
                and visible(unicodedata.normalize('NFD', actual)) != visible(canonical)):
            return False
        decomposed = []
        cursor = 0
        for item in fields:
            text = _decompose_codepoints(item.text)
            end = cursor + len(visible(text))
            decomposed.append(item._replace(text=text, source=_decompose_codepoints(item.source),
                                            start=cursor, end=end))
            cursor = end
        boundary_offsets = {old.start: new.start for old, new in zip(fields, decomposed)}
        boundary_offsets.update({old.end: new.end for old, new in zip(fields, decomposed)})
        replacement_boundaries = {boundary_offsets[index] for index in replacement_boundaries}
        fields = decomposed
        offset = cursor
        actual = ''.join(item.text for item in fields)
    actual_gaps, expected_gaps = _gaps(actual), _gaps(canonical)
    field_gaps = [_gaps(f.text) for f in fields]
    changes: dict[int, tuple[str, int | None]] = {}
    wanted: dict[int, str] = {}
    for position in actual_gaps.keys() | expected_gaps.keys():
        candidates = [i for i, f in enumerate(fields) if f.start <= position <= f.end]
        existing = [i for i in candidates if field_gaps[i].get(position - fields[i].start)]
        if position in replacement_boundaries:
            # Replacement erases a node's entire content, but keeps its tail.
            # Resolve ownership against that final projection before comparing
            # gaps; correct-looking padding inside a token would otherwise vanish.
            candidates = [i for i in candidates if not any(
                node.tag in replaced_tags for node in fields[i].path)]
        interior = [i for i in candidates if fields[i].start < position < fields[i].end]
        owners = [i for i in candidates if fields[i] in _source_owners(fields, position)]
        surviving = [i for i in existing if i in candidates]
        # Resolve ownership before choosing normalization. Source ownership wins
        # over model placement in either direction across a preservation boundary.
        relevant = interior or owners or surviving or candidates
        target = relevant[0] if len(relevant) == 1 else None
        protected_modes = {fields[i].mode for i in relevant if not collapses_breaks(fields[i].mode)}
        mode = next(iter(protected_modes)) if len(protected_modes) == 1 else WhiteSpace.PRE
        protected = bool(protected_modes)
        if target is None and owners and any(i not in owners for i in existing) and (
                protected or any(not collapses_breaks(fields[i].mode) for i in existing)):
            return False  # A cross-mode move with multiple source owners is ambiguous.
        expected = normalize(expected_gaps[position], mode if protected else WhiteSpace.NORMAL)
        if not protected and position in (0, offset):
            expected = ''  # Normal paragraph edges, not independently trimmed XML nodes.
        if expected:
            wanted[position] = expected
        misplaced = any(i not in candidates for i in existing) or (
            target is not None and any(i != target for i in existing))
        if actual_gaps[position] == expected and not misplaced:
            continue
        if expected and target is None:
            if protected:
                return False  # Canonical text cannot allocate multiple preserved fields.
            if surviving and position not in replacement_boundaries:
                target = surviving[0]  # CSS collapses a normal run across inline boundaries.
            else:
                # An empty replacement can later restore visible content between
                # two currently adjacent gaps. Their positions are not equivalent.
                return False
        changes[position] = expected, target
    values = [f.text for f in fields]
    for position, (expected, target) in sorted(changes.items()):
        candidates = [i for i, f in enumerate(fields) if f.start <= position <= f.end]
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
    recovered = ''.join(values)
    if dict(_gaps(recovered)) != wanted:
        return False
    # Matching gap offsets alone does not prove equivalent text: combining
    # marks can reorder inside a cluster, but never across its whitespace.
    # Build the expected stream with the source-context whitespace decisions
    # and verify the complete candidate before atomically changing any field.
    characters = visible(canonical)
    expected_text = ''.join(wanted.get(index, '') + char for index, char in enumerate(characters))
    expected_text += wanted.get(len(characters), '')
    if unicodedata.normalize('NFD', recovered) != unicodedata.normalize('NFD', expected_text):
        return False
    # All recovery succeeded before changing any response fields.
    for f, value in zip(fields, values):
        setattr(f.element, f.attribute, value or None)
    return True
