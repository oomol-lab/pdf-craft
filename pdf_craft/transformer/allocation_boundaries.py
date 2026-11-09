"""Expand compact engineering boundaries without regenerating the artist's prose."""
from bisect import bisect_left

from ..common.whitespace import normalize_whitespace
from .canonical_math import CanonicalMath


def _view(raw):
    """Use the shared prose policy, retaining original character coordinates."""
    chars, starts, ends = [], [], []
    for index, char in enumerate(raw):
        value = normalize_whitespace(char)
        if not value:
            continue
        if value == ' ' and chars and chars[-1] == ' ':
            ends[-1] = index + 1
        else:
            chars.append(value)
            starts.append(index)
            ends.append(index + 1)
    return ''.join(chars), starts, ends


def _matches(raw, anchor, cursor, view, protected, strict):
    text, starts, ends = view
    needle = anchor if strict else normalize_whitespace(anchor)
    if not needle:
        return
    haystack = raw if strict else text
    start = haystack.find(needle, cursor if strict else bisect_left(starts, cursor))
    while start >= 0:
        finish = start + len(needle)
        left, right = (start, finish) if strict else (starts[start], ends[finish-1])
        # Even in ordinary prose, a suffix touching TeX must copy its syntax.
        if strict or not any(a < right and left < b for a, b in protected) or raw[left:right] == anchor:
            yield right, finish
        start = haystack.find(needle, start + 1)


def _follows(raw, after, end, position, view, protected, strict):
    if strict:
        return raw.startswith(after, end)
    text, _, ends = view
    needle = normalize_whitespace(after)
    if not text.startswith(needle, position):
        return False
    finish = ends[position+len(needle)-1] if needle else end
    if any(a < finish and end < b for a, b in protected):
        return raw.startswith(after, end)
    return True


def expand_boundaries(units: list[dict], raw: str, *, preserved_ids: frozenset[str] = frozenset()) -> list[dict]:
    """Resolve unambiguous prose suffixes without requiring exact whitespace.

    IDs still choose semantic owners. An anchor only selects the end of the
    next contiguous slice; the program never chooses an owner or repairs a
    missing passage. Full-text allocations retain their existing validation.
    """
    if not any('end' in unit for unit in units):
        return units
    view = _view(raw)
    protected = [match.span() for match in CanonicalMath.matches(raw)]
    expanded, cursor = [], 0
    for unit in units:
        if 'end' not in unit:
            if unit.get('text') != '':
                raise ValueError('Use end anchors for every nonempty allocation in a compact response.')
            expanded.append(unit)
            continue
        anchor = unit['end']
        if 'text' in unit or not isinstance(anchor, str) or not normalize_whitespace(anchor, trim=True):
            raise ValueError('An end anchor must be a nonempty exact suffix, without a text field.')
        after = unit.get('after', '')
        if not isinstance(after, str):
            raise ValueError('Boundary after context must be a string.')
        strict = unit['id'] in preserved_ids
        candidates = [end for end, position in _matches(raw, anchor, cursor, view, protected, strict)
                      if _follows(raw, after, end, position, view, protected,
                                  strict or bool(preserved_ids))]
        if len(candidates) != 1:
            raise ValueError(f"Unit {unit['id']}: end anchor {anchor!r} is absent or ambiguous "
                             f"after translation offset {cursor} ({len(candidates)} matches); "
                             'include more adjacent preceding text or exact following after context.')
        end = candidates[0]
        expanded.append(dict(unit, text=raw[cursor:end]))
        cursor = end
    if normalize_whitespace(raw[cursor:], trim=True):
        raise ValueError(f'End anchors omit translation after offset {cursor}: {raw[cursor:cursor+160]!r}. '
                         'The final nonempty allocation must reach the end of the translation.')
    # Keep trailing whitespace with its last real owner, including PRE text.
    for unit in reversed(expanded):
        if 'end' in unit:
            unit['text'] += raw[cursor:]
            break
    return expanded
