"""Allocate a structural paragraph gap separately from preserved code edges."""
from .whitespace import collapses_breaks, normalize


def structural_gap(fields, field_gaps, candidates, position, canonical_gap, virtual_roots):
    """Return field assignments only for a uniquely recoverable pre/block edge.

    Paragraph separators are virtual fields, never persisted code padding.
    Preserve existing edge whitespace when it matches canonical; an explicit
    blank line can delimit a single existing code-edge owner. Multiple code
    owners remain ambiguous and fall through to the ordinary ownership checks.
    This does not normalize or allocate whitespace inside a code field.
    """
    virtual = [i for i in candidates if fields[i].path[0] in virtual_roots]
    protected = [i for i in candidates if fields[i].path[0].tag == 'pre'
                 and not collapses_breaks(fields[i].mode)]
    if len(virtual) != 1 or not protected:
        return None
    separator = virtual[0]
    left = [i for i in protected if i < separator]
    right = [i for i in protected if i > separator]
    gaps = {i: field_gaps[i].get(position-fields[i].start, '') for i in protected}
    prefix = ''.join(gaps[i] for i in left)
    suffix = ''.join(gaps[i] for i in right)
    edges_match = (len(prefix)+len(suffix) <= len(canonical_gap)
                   and canonical_gap.startswith(prefix) and canonical_gap.endswith(suffix))
    if '\n\n' in canonical_gap and not edges_match:
        left_owners = [i for i in left if gaps[i]]
        right_owners = [i for i in right if gaps[i]]
        if len(left_owners) == 1 and not right:
            prefix = canonical_gap.partition('\n\n')[0]
            gaps[left_owners[0]] = prefix
        elif len(right_owners) == 1 and not left:
            suffix = canonical_gap.rpartition('\n\n')[2]
            gaps[right_owners[0]] = suffix
    if (len(prefix)+len(suffix) > len(canonical_gap)
            or not canonical_gap.startswith(prefix) or not canonical_gap.endswith(suffix)):
        return None
    end = len(canonical_gap)-len(suffix) if suffix else len(canonical_gap)
    return {**gaps, separator: normalize(canonical_gap[len(prefix):end])}
