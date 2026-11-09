"""Recover known canonical whitespace only when its slot ownership is unique."""
from collections import defaultdict
from difflib import SequenceMatcher
import unicodedata
from typing import NamedTuple
from xml.etree.ElementTree import Element

from ..common.whitespace import is_whitespace, normalize_whitespace
from .whitespace import WhiteSpace, collapses_breaks, mode_for, normalize
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
        if is_whitespace(char):
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
        (position == f.start and is_whitespace(f.source[0]))
        or (position == f.end and is_whitespace(f.source[-1])))]


def restore_canonical_whitespace(roots: list[Element], canonical: str,
                                 sources: list[Element] | None = None,
                                 modes: list[WhiteSpace] | None = None, *,
                                 replaced_tags: frozenset[str] = frozenset(),
                                 join_roots: bool = False,
                                 transparent_tags: frozenset[str] = frozenset(),
                                 formatting_tags: frozenset[str] = frozenset(),
                                 virtual_roots: frozenset[Element] = frozenset()) -> bool:
    """Align unchanged characters, then reconstruct legal gaps transactionally.

    Non-whitespace characters remain immutable. Normal prose collapses Unicode
    spaces and removes explicit padding; preserved code/TeX retains its gaps.
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
        end = offset + sum(not is_whitespace(char) for char in text)
        fields.append(_Field(element, attribute, path, mode, source or '', text, offset, end))
        offset = end

    def walk(element, source, inherited, path):
        start = offset
        mode = (inherited if element.tag in formatting_tags else
                mode_for(source if source is not None else element, inherited))
        field_source = source if element.tag not in formatting_tags else None
        field(element, 'text', path, mode, field_source.text if field_source is not None else '')
        originals = {} if source is None else {
            id(child): original for original, child in match_children(
                ((node.tag, element_id(node), node) for node in source), element,
            )
        }
        for child in element:
            original = originals.get(id(child))
            # A provided source is authoritative. A missing match must never
            # let model-supplied attributes override its preservation context.
            if source is not None and original is None and child.tag not in transparent_tags:
                return False
            if not walk(child, original, mode, path + [child]):
                return False
            field(child, 'tail', path, mode, original.tail if original is not None else '')
        if element.tag in replaced_tags:
            replacement_boundaries.update((start, offset))
        return True

    if sources is not None and len(sources) != len(roots):
        return False
    root_boundaries = {}
    for index, root in enumerate(roots):
        if index and fields:
            root_boundaries[offset] = len(fields) - 1
        if not walk(root, sources[index] if sources is not None else None,
                    modes[index] if modes is not None else WhiteSpace.NORMAL, [root]):
            return False
    actual = ''.join(item.text for item in fields)
    canonical = canonical.replace('\r\n', '\n').replace('\r', '\n')
    visible = lambda text: ''.join(c for c in text if not is_whitespace(c))
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
        root_boundaries = {boundary_offsets[index]: field_index
                           for index, field_index in root_boundaries.items()}
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
        if (target is None and join_roots and position in root_boundaries
                and position not in replacement_boundaries and not owners and not surviving):
            # These roots are slots of ONE adapter-defined text unit, not
            # independent paragraphs. A new cosmetic boundary gap belongs to
            # the preceding slot's final surviving field, never a block tail
            # that the submitter would drop.
            candidate = root_boundaries[position]
            if all(collapses_breaks(fields[i].mode) for i in candidates):
                target = candidate
        if target is None and formatting_tags and position in replacement_boundaries and candidates:
            # Temporary expression containers disappear during typed projection.
            # Several gap fields can therefore be the SAME final text position.
            # Preserve genuine rich-inline/code ownership distinctions.
            paths = {tuple(id(node) for node in fields[i].path if node.tag not in formatting_tags)
                     for i in candidates}
            if len(paths) == 1:
                target = (surviving or owners or candidates)[0]
                relevant = [target]
        virtual = [i for i in candidates if fields[i].path[0] in virtual_roots]
        if virtual and all(collapses_breaks(fields[i].mode) for i in candidates):
            # Paragraph separators are rendered by the document structure.
            # Keep their ordinary gap outside persisted owners, rather than
            # adding trailing spaces to a reference mark or neighboring note.
            target = virtual[0]
            relevant = [target]
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
                if not is_whitespace(char) or cursor != position:
                    result.append(char)
                if not is_whitespace(char):
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


def canonical_difference(canonical: str, blocks: list[Element]) -> str:
    """Bounded actionable XML fill diagnostics, without judging the artist."""
    visible = lambda value: ''.join(char for char in value if not is_whitespace(char))
    expected = visible(unicodedata.normalize('NFD', canonical))
    texts = [visible(unicodedata.normalize('NFD', ''.join(block.itertext()))) for block in blocks]
    actual = ''.join(texts)
    edits = [op for op in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes()
             if op[0] != 'equal']
    missing = sum(right-left for _, left, right, _, _ in edits)
    added = sum(right-left for _, _, _, left, right in edits)
    if edits:
        prefix = next((index for index, pair in enumerate(zip(expected, actual)) if pair[0] != pair[1]), min(len(expected), len(actual)))
        locations = _diagnostic_locations(blocks, prefix, min(prefix+1, len(actual)))
        detail = (f' First differing character at translation/XML non-whitespace offset {prefix}.'
                  f' Expected from here {expected[prefix:prefix+180]!r}.'
                  f' Received from here {actual[prefix:prefix+180]!r}.'
                  f' Candidate fields: {"; ".join(locations[:6])}.')
        # Include actual edit spans, not just an ambiguous context excerpt.
        # These are diagnostics only: the XML engineer decides ownership and
        # identity; no diff is automatically applied to the accepted candidate.
        for operation, left, right, start, end in edits[:10]:
            locations = _diagnostic_locations(blocks, start, end)
            detail += (f' Edit {operation} at XML [{start}:{end}] / translation [{left}:{right}]: '
                       f'expected={expected[left:right][:140]!r}, received={actual[start:end][:140]!r}; '
                       f'fields={"; ".join(locations[:3])}.')
        if len(edits) > 10:
            detail += f' {len(edits)-10} additional edit spans remain; preserve the entire original artist text.'
    else:
        old = normalize_whitespace(unicodedata.normalize('NFD', canonical), trim=True)
        new = normalize_whitespace(unicodedata.normalize('NFD', ''.join(''.join(block.itertext()) for block in blocks)), trim=True)
        expected_gaps, actual_gaps = _gaps(old), _gaps(new)
        positions = sorted(position for position in expected_gaps.keys() | actual_gaps.keys()
                           if expected_gaps[position] != actual_gaps[position])
        if positions:
            position = positions[0]
            cursor, locations = 0, []
            def fields(node):
                yield node, 'text', node.text or ''
                for child in node:
                    yield from fields(child)
                    yield child, 'tail', child.tail or ''
            for block in blocks:
                for node, field, value in fields(block):
                    end = cursor + len(visible(unicodedata.normalize('NFD', value)))
                    if cursor <= position <= end:
                        locations.append(f'block {block.get("id", "separator")}: <{node.tag} id={node.get("id", "none")}>.{field}={value[:90]!r}')
                    cursor = end
            detail = (f' Whitespace difference at non-whitespace offset {position}: '
                      f'expected gap {expected_gaps[position]!r}, received {actual_gaps[position]!r}.'
                      f' Text near boundary: {expected[max(0, position-60):position]!r} | {expected[position:position+90]!r}.'
                      f' Candidate fields: {"; ".join(locations[:8])}.'
                      ' Put the gap explicitly in the correct surrounding text/tail, outside empty reference or anchor tags.')
        else:
            detail = ' Characters match, but whitespace ownership or protected code/TeX gaps are ambiguous. Preserve protected gaps and keep visible spaces outside empty structural tokens.'
    return ('Visible text differs from the canonical translation after whitespace normalization.'
            f' Missing {missing}, added {added} non-whitespace characters.'
            + detail + ' Fill the complete XML from the same original translation; do not rewrite it.')


def _diagnostic_locations(blocks, start, finish):
    """Bounded XML field provenance for flattened non-whitespace edit positions."""
    cursor, locations = 0, []
    def walk(node, path):
        yield node, 'text', node.text or '', path
        for child in node:
            yield from walk(child, path + [child])
            yield child, 'tail', child.tail or '', path
    for block in blocks:
        for node, attribute, value, path in walk(block, [block]):
            length = sum(not is_whitespace(char) for char in unicodedata.normalize('NFD', value))
            end = cursor+length
            if length and ((cursor <= start <= end) if start == finish else cursor < finish and start < end):
                identities = [part.get('keys', part.get('key')) for part in path if part.tag == 'source-math']
                locations.append(f'XML block id={block.get("id", "separator")}: '
                                 f'<{node.tag} id={node.get("id", "none")} keys={identities}>.{attribute}={value[:180]!r}')
            cursor = end
    return locations
