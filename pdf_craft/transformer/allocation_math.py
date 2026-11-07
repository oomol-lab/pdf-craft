"""Bind source identities to engineering-selected translated occurrences."""
import json
from .canonical_projection import MathProjectionError, SourceMath
from .canonical_math import CanonicalMath
from .allocation_text import formula_occurrences
from .whitespace import normalize
from .math_binding import partition_math


def _prose(value):
    return normalize(value, trim=True)


def _source_keys(item):
    declared = item.get('keys', [] if item.get('preserve') is True or 'parts' in item else [item.get('key')])
    if not isinstance(declared, list) or any(not isinstance(key, str) for key in declared):
        raise ValueError('Source keys must be an array of strings.')
    if 'parts' not in item:
        return declared
    parts = item['parts']
    if not isinstance(parts, list) or not parts or any(not isinstance(part, dict) for part in parts):
        raise ValueError('Math parts must be a nonempty array of objects.')
    if any(not isinstance(part.get('keys', []), list)
           or any(not isinstance(key, str) for key in part.get('keys', [])) for part in parts):
        raise ValueError('Each mathematical part needs an array of source keys.')
    keys = [key for part in parts for key in part.get('keys', [])]
    # Parts are the complete submitted correspondence, not an incremental
    # change to the outer shorthand. Never ignore a nested source identity.
    if any(key not in keys for key in declared) or ('key' in item and item['key'] not in keys):
        raise ValueError('Outer source keys must agree with identities explicitly assigned in parts.')
    return keys


def allocate_math(unit, math, targets):
    text = unit['text']
    values = math.values if math is not None else {}
    formulas = unit.get('formulas', [])
    if not isinstance(formulas, list) or any(not isinstance(item, dict) for item in formulas):
        raise ValueError('Formula allocations must be an array of objects.')
    for item in formulas:
        if item.get('preserve') is True and (_source_keys(item) or 'key' in item or 'keys' in item):
            raise ValueError('Additional translated math must not claim a source identity.')
        keys = _source_keys(item)
        if not isinstance(keys, list) or (not keys and item.get('preserve') is not True) or any(not isinstance(key, str) for key in keys):
            raise ValueError('Each formula allocation must specify nonempty source keys.')
        if 'target' in item:
            if not isinstance(item['target'], str):
                raise ValueError('Formula target must be a translated occurrence id.')
        elif any(not isinstance(item.get(field), str) for field in ('text', 'before', 'after')):
            raise ValueError('Formula text and before/after context must be strings.')
    assigned = [key for item in formulas for key in _source_keys(item)]
    if sorted(assigned) != sorted(values):
        missing = sorted(set(values)-set(assigned))
        source_values = {key: values[key].latex for key in missing}
        available = [key for key, (owner, _) in targets.items() if owner == unit['id']]
        raise ValueError(f'Assign each source identity once. Missing: {missing}; duplicated: {sorted({key for key in assigned if assigned.count(key)>1})}; unknown: {sorted(set(assigned)-set(values))}. '
                         f'Missing source contents: {json.dumps(source_values, ensure_ascii=False)}. '
                         f'Available target ids in this unit: {available}; allocated text excerpt: {text[:300]!r}. '
                         'Additional mathematical occurrences must use preserve=true instead of reusing a source key.')
    selected_targets = [item['target'] for item in formulas if 'target' in item]
    if len(set(selected_targets)) != len(selected_targets):
        raise ValueError('Formula allocations cannot overlap; combine source keys for one target.')
    edits, bound, issues = [], [], []
    for item in formulas:
        keys = _source_keys(item)
        if 'target' in item:
            entry = targets.get(item['target'])
            if entry is None or entry[0] != unit['id']:
                available = [key for key, (owner, _) in targets.items() if owner == unit['id']]
                issues.append(f"Formula {keys}: target {item['target']!r} is not in unit {unit['id']}; available targets={available}. "
                              f"Defined global target ids={list(targets)}; allocated text excerpt={text[:300]!r}.")
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
        group: list[SourceMath | str] = [values[key] for key in keys]
        original = matches[0].group()
        complete = CanonicalMath.matches(original)
        delimited = len(complete) == 1 and complete[0].group() == original
        canonical = original if delimited else '\\(' + original + '\\)'
        if group:
            parts = item.get('parts')
            if parts is not None:
                if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
                    raise ValueError('Math parts must be an array of objects.')
                if any(not isinstance(part.get('keys', []), list) for part in parts):
                    raise ValueError('Each mathematical part needs an array of source keys.')
                parts = [dict(text=part.get('text'), keys=[keys.index(key) if key in keys else -1
                              for key in part.get('keys', [])]) for part in parts]
            try:
                group = partition_math(canonical, [values[key] for key in keys], parts, require_parts='target' in item)
            except ValueError as error:
                issues.append(f'Formula {keys}: {error}')
                continue
        elif 'parts' in item:
            raise ValueError('Math parts must bind source identities.')
        edits.append((matches[0].start(), matches[0].end(), group, canonical))
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
