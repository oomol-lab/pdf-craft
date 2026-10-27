"""Restore canonical padding at temporary formula boundaries, never wording."""
from collections import Counter
from xml.etree.ElementTree import Element
from typing import NamedTuple

from .xml_translator.utils import normalize_whitespace


class _TextSlot(NamedTuple):
    element: Element
    attribute: str
    path: list[Element]
    root: Element
    start: int
    end: int
    text: str
    plain: str


def _gaps(text):
    offset, gaps = 0, Counter()
    for char in text:
        if char == ' ':
            gaps[offset] += 1
        else:
            offset += 1
    return gaps


def restore_formula_boundary_whitespace(roots: list[Element], canonical: str) -> bool:
    """Restore padding only if all nonspace characters and other gaps match."""
    slots: list[_TextSlot] = []
    boundaries, root_ends = set(), set()
    offset = 0

    def slot(element, attribute, path, root):
        nonlocal offset
        text = normalize_whitespace(getattr(element, attribute) or '')
        plain = text.replace(' ', '')
        slots.append(_TextSlot(element, attribute, path, root, offset, offset + len(plain), text, plain))
        offset += len(plain)

    def walk(element, path, root):
        start = offset
        slot(element, 'text', path, root)
        for child in element:
            walk(child, path + [child], root)
            slot(child, 'tail', path, root)
        if element.tag == 'expression':
            boundaries.update((start, offset))

    for root in roots:
        walk(root, [root], root)
        root_ends.add(offset)
    actual = ''.join(item.text for item in slots)
    if actual.replace(' ', '') != canonical.replace(' ', ''):
        return False
    actual_gaps, expected_gaps = _gaps(actual), _gaps(canonical)
    differences = {p for p in actual_gaps.keys() | expected_gaps.keys()
                   if actual_gaps[p] != expected_gaps[p]}
    if not differences or not differences <= boundaries or differences & (root_ends - {offset}):
        return False

    values, fields = {}, {}
    for item in slots:
        element, attribute, _, _, cursor, _, text, _ = item
        chars = []
        for char in text:
            if char != ' ' or cursor not in differences:
                chars.append(char)
            if char != ' ':
                cursor += 1
        key = id(element), attribute
        values[key], fields[key] = ''.join(chars), item
    content = [item for item in slots if item.plain]
    for position in differences:
        if not expected_gaps[position]:
            continue
        if position == 0:
            target = fields[id(roots[0]), 'text']
        elif position == offset:
            root = roots[-1]
            target = fields[id(root[-1]), 'tail'] if len(root) else fields[id(root), 'text']
        else:
            left = next(s for s in reversed(content) if s.end == position)
            right = next(s for s in content if s.start == position)
            common = 0
            for a, b in zip(left.path, right.path):
                if a is not b:
                    break
                common += 1
            # Put a separator outside the expression that source restoration replaces.
            target = fields[id(left.path[common]), 'tail'] if common < len(left.path) else left
        key = id(target.element), target.attribute
        value, index, remaining = values[key], 0, position - target.start
        while remaining:
            if value[index] != ' ':
                remaining -= 1
            index += 1
        values[key] = value[:index] + ' ' * expected_gaps[position] + value[index:]
    if ''.join(values[id(s.element), s.attribute] for s in slots) != canonical:
        return False
    for element, attribute, *_ in slots:
        setattr(element, attribute, values[id(element), attribute] or None)
    return True
