"""Recover engineering allocation whitespace and describe actual text loss."""
from difflib import SequenceMatcher
import re
from xml.etree.ElementTree import Element, SubElement

from .canonical_math import CanonicalMath
from .canonical_text import restore_canonical_whitespace
from ..common.whitespace import is_whitespace


def formula_occurrences(text, value):
    """Locate engineer spans; always return the unchanged artist substring.

    A copied delimited expression may have cosmetic whitespace mistakes. The
    shared recovery checks against each actual complete mathematical span;
    multiple equivalent occurrences still require semantic disambiguation.
    """
    exact = list(re.finditer(re.escape(value), text))
    if exact:
        return exact
    parsed = CanonicalMath.matches(value)
    if len(parsed) != 1 or parsed[0].group() != value:
        return []
    result = []
    for match in CanonicalMath.matches(text):
        candidate = Element('expression', {'xml:space': 'preserve'})
        candidate.text = value
        if restore_canonical_whitespace([candidate], match.group(), sources=[candidate]):
            result.append(match)
    return result


def _root(text, preserve):
    root = Element('allocation', {'xml:space': 'preserve'} if preserve else {})
    cursor = 0
    previous = None
    for match in CanonicalMath.matches(text):
        if previous is None:
            root.text = text[cursor:match.start()]
        else:
            previous.tail = text[cursor:match.start()]
        previous = SubElement(root, 'expression', {'xml:space': 'preserve'})
        previous.text = match.group()
        cursor = match.end()
    if previous is None:
        root.text = text
    else:
        previous.tail = text[cursor:]
    return root


def recover_allocation_text(units, calls, raw):
    """Recover only uniquely owned whitespace, using the shared XML rules.

    The artist response is never changed. These are engineering text slots;
    formulas and explicitly preserved source contexts retain their whitespace.
    """
    roots = [_root(unit['text'], getattr(calls[int(unit['id'])], 'preserve_space', False))
             for unit in units]
    if not restore_canonical_whitespace(roots, raw, sources=roots, join_roots=True):
        return None
    return [''.join(root.itertext()) for root in roots]


def allocation_difference(units, raw):
    """Bounded, actionable diagnostics; never a translation-quality verdict."""
    visible = lambda value: ''.join(char for char in value if not is_whitespace(char))
    expected = visible(raw)
    texts = [visible(unit['text']) for unit in units]
    actual = ''.join(texts)
    edits = [op for op in SequenceMatcher(None, expected, actual, autojunk=False).get_opcodes()
             if op[0] != 'equal']
    missing = sum(right-left for _, left, right, _, _ in edits)
    added = sum(right-left for _, _, _, left, right in edits)
    lengths = ', '.join(f"id={unit['id']}:{len(unit['text'])}" for unit in units)
    detail = ''
    if edits:
        _, left, right, start, end = edits[0]
        offset, owner = 0, units[-1]['id'] if units else '?'
        for unit, text in zip(units, texts):
            if start < offset + len(text):
                owner = unit['id']
                break
            offset += len(text)
        def context(value, begin, finish):
            positions = [i for i, char in enumerate(value) if not is_whitespace(char)] + [len(value)]
            return value[positions[max(0, begin-40)]:positions[min(len(positions)-1, max(begin+80, min(finish, begin+500)))]]
        detail = (f' First difference near unit {owner}, non-whitespace translation offset {left}, allocation offset {start}.'
                  f' Expected context: {context(raw, left, right)!r}.'
                  f' Received context: {context("".join(unit["text"] for unit in units), start, end)!r}.')
    else:
        detail = ' Characters match, but whitespace ownership/preservation could not be recovered unambiguously.'
    return ('Allocation must preserve the complete original translation, in order, without changing words.'
            f' Missing {missing}, added {added} non-whitespace characters.'
            f' Translation length={len(raw)}, allocated length={sum(len(unit["text"]) for unit in units)}.'
            + detail + f' Empty unit ids: {[unit["id"] for unit in units if all(is_whitespace(char) for char in unit["text"])]}.'
            + f' Allocated unit lengths: {lengths}.')


def translated_math_evidence(raw):
    """Engineering-only occurrence IDs, assigned AFTER free translation."""
    return [dict(id=f't{index}', text=match.group(),
                 before=raw[max(0, match.start()-60):match.start()],
                 after=raw[match.end():match.end()+60])
            for index, match in enumerate(CanonicalMath.matches(raw), 1)]


def translated_math_targets(units, raw):
    """Map verified allocated ranges to immutable translated occurrences.

    Unit array order is translated reading order, not source owner order.
    Offsets ignore recoverable document whitespace and canonical Unicode form;
    no source semantics or formula values choose an identity here.
    """
    import unicodedata
    def length(value):
        return len(''.join(char for char in unicodedata.normalize('NFD', value)
                           if not is_whitespace(char)))
    local, offset = {}, 0
    for unit in units:
        for match in CanonicalMath.matches(unit['text']):
            key = offset + length(unit['text'][:match.start()]), offset + length(unit['text'][:match.end()])
            local[key] = unit['id'], match
        offset += length(unit['text'])
    targets = {}
    for index, match in enumerate(CanonicalMath.matches(raw), 1):
        key = length(raw[:match.start()]), length(raw[:match.end()])
        if key in local:
            targets[f't{index}'] = local[key]
    return targets
