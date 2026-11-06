"""Bind source identities to engineering-selected translated occurrences."""
import json
from .canonical_projection import MathProjectionError, formula_content
from .canonical_math import CanonicalMath
from .allocation_text import formula_occurrences
from .whitespace import normalize


def _prose(value):
    return normalize(value, trim=True)


def allocate_math(unit, math, targets):
    text = unit['text']
    values = math.values if math is not None else {}
    formulas = unit.get('formulas', [])
    if not isinstance(formulas, list) or any(not isinstance(item, dict) for item in formulas):
        raise ValueError('Formula allocations must be an array of objects.')
    for item in formulas:
        if item.get('preserve') is True and ('key' in item or 'keys' in item):
            raise ValueError('Additional translated math must not claim a source identity.')
        keys = item.get('keys', [] if item.get('preserve') is True else [item.get('key')])
        if not isinstance(keys, list) or (not keys and item.get('preserve') is not True) or any(not isinstance(key, str) for key in keys):
            raise ValueError('Each formula allocation must specify nonempty source keys.')
        if 'target' in item:
            if not isinstance(item['target'], str):
                raise ValueError('Formula target must be a translated occurrence id.')
        elif any(not isinstance(item.get(field), str) for field in ('text', 'before', 'after')):
            raise ValueError('Formula text and before/after context must be strings.')
    assigned = [key for item in formulas for key in item.get('keys', [] if item.get('preserve') is True else [item.get('key')])]
    if sorted(assigned) != sorted(values):
        raise ValueError(f'Assign each source identity once. Missing: {sorted(set(values)-set(assigned))}; duplicated: {sorted({key for key in assigned if assigned.count(key)>1})}; unknown: {sorted(set(assigned)-set(values))}. Additional mathematical occurrences must use preserve=true instead of reusing a source key.')
    edits, bound, issues = [], [], []
    for item in formulas:
        keys = item.get('keys', [] if item.get('preserve') is True else [item.get('key')])
        if 'target' in item:
            entry = targets.get(item['target'])
            if entry is None or entry[0] != unit['id']:
                available = [key for key, (owner, _) in targets.items() if owner == unit['id']]
                issues.append(f"Formula {keys}: target {item['target']!r} is not in unit {unit['id']}; available targets={available}.")
                continue
            matches = [entry[1]]
        else:
            value, before, after = item['text'], item['before'], item['after']
            if not value:
                issues.append(f'Formula {keys}: a span must name nonempty translated text.')
                continue
            occurrences = formula_occurrences(text, value)
            matches = occurrences if len(occurrences) == 1 else [match for match in occurrences
                       if _prose(text[:match.start()]).endswith(_prose(before))
                       and _prose(text[match.end():]).startswith(_prose(after))]
            if len(matches) != 1:
                candidates = [dict(before=text[max(0, match.start()-60):match.start()],
                                   text=match.group(), after=text[match.end():match.end()+60])
                              for match in occurrences[:8]]
                available = {key: match.group() for key, (owner, match) in targets.items() if owner == unit['id']}
                issues.append(f'Formula {keys}: span {value!r} with before={before!r}, after={after!r} matches {len(matches)} occurrences. Literal occurrences={len(occurrences)}. Exact candidate contexts: {json.dumps(candidates, ensure_ascii=False)}. Translated targets: {json.dumps(available, ensure_ascii=False)}')
                continue
        edits.append((matches[0].start(), matches[0].end(), [values[key] for key in keys], matches[0].group()))
    if issues:
        raise MathProjectionError('\n'.join(issues))
    for match in CanonicalMath.matches(text):
        if any(start <= match.start() and end >= match.end() for start, end, *_ in edits):
            continue
        if any(start < match.end() and end > match.start() for start, end, *_ in edits):
            raise ValueError(f'Use complete mathematical delimiters in the formula span: {match.group()!r}.')
        # Additional artist math has no source identity to restore. Keeping
        # it verbatim is not a source match or a translation quality gate.
        edits.append((match.start(), match.end(), [], match.group()))
    edits.sort(key=lambda item: item[0])
    cursor, parts, projected_formulas = 0, [], []
    for start, end, group, original in edits:
        if start < cursor:
            raise ValueError('Formula allocations cannot overlap.')
        if not group:
            if len(CanonicalMath.matches(original)) != 1 or CanonicalMath.matches(original)[0].group() != original:
                raise ValueError('A preserved additional mathematical occurrence needs its complete original delimiters.')
            latex = original
        else:
            latex = (group[0].latex if len(group) == 1 else '\\(' + ''.join(formula_content(source.latex)[0] for source in group) + '\\)')
        if len(CanonicalMath.matches(original)) == 1 and CanonicalMath.matches(original)[0].group() == original:
            latex = original
        parts.extend((text[cursor:start], latex))
        projected_formulas.append(latex)
        bound.append(group)
        cursor = end
    parts.append(text[cursor:])
    canonical = ''.join(parts)
    if math is not None and [match.group() for match in CanonicalMath.matches(canonical)] != projected_formulas:
        raise MathProjectionError('Account for all mathematical occurrences and their complete delimiters.')
    return canonical, bound
