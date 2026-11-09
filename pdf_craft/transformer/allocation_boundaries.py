"""Expand compact engineering boundaries without regenerating the artist's prose."""


def expand_boundaries(units: list[dict], raw: str) -> list[dict]:
    """Resolve exact, unambiguous suffix anchors in translated reading order.

    IDs still choose semantic owners. An anchor only selects the end of the
    next contiguous slice; the program never chooses an owner or repairs a
    missing passage. Full-text allocations retain their existing validation.
    """
    if not any('end' in unit for unit in units):
        return units
    expanded, cursor = [], 0
    for unit in units:
        if 'end' not in unit:
            if unit.get('text') != '':
                raise ValueError('Use end anchors for every nonempty allocation in a compact response.')
            expanded.append(unit)
            continue
        anchor = unit['end']
        if 'text' in unit or not isinstance(anchor, str) or not anchor.strip():
            raise ValueError('An end anchor must be a nonempty exact suffix, without a text field.')
        after = unit.get('after', '')
        if not isinstance(after, str):
            raise ValueError('Boundary after context must be a string.')
        candidates, start = [], raw.find(anchor, cursor)
        while start >= 0:
            end = start + len(anchor)
            if raw.startswith(after, end):
                candidates.append(end)
            start = raw.find(anchor, start + 1)
        if len(candidates) != 1:
            raise ValueError(f"Unit {unit['id']}: end anchor {anchor!r} is absent or ambiguous "
                             f"after translation offset {cursor} ({len(candidates)} matches); "
                             'include more adjacent preceding text or exact following after context.')
        end = candidates[0]
        expanded.append(dict(unit, text=raw[cursor:end]))
        cursor = end
    if raw[cursor:].strip(' \t\r\n'):
        raise ValueError(f'End anchors omit translation after offset {cursor}: {raw[cursor:cursor+160]!r}. '
                         'The final nonempty allocation must reach the end of the translation.')
    # Keep trailing whitespace with its last real owner, including PRE text.
    for unit in reversed(expanded):
        if 'end' in unit:
            unit['text'] += raw[cursor:]
            break
    return expanded
