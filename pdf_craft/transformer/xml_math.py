"""Read formula identities directly from accepted XML fill, then project typed nodes."""
from copy import deepcopy
from dataclasses import dataclass, replace
from xml.etree.ElementTree import Element

from ..common.whitespace import is_whitespace
from .canonical_math import CanonicalMath
from .canonical_projection import MathProjectionError, SourceMath, formula_content
from .math_binding import unchanged_formula, literal_percent
from .xml_translator.segment import TextPosition, TextSegment, combine_text_segments, find_block_depth, search_text_segments
from .xml_translator.xml import is_inline_element

BINDING_TAG = 'source-math'


@dataclass(frozen=True)
class Binding:
    start: int
    end: int
    source: SourceMath
    text: str


def binding_spans(root: Element, math) -> dict[int, list[Binding]]:
    values = math.values if math is not None else {}
    seen = set()
    result = {}
    def block_spans(block):
        offset, bindings = 0, []
        def walk(node):
            nonlocal offset
            start = offset
            offset += len(node.text or '')
            for child in node:
                walk(child)
                offset += len(child.tail or '')
            if node.tag == BINDING_TAG:
                key = node.get('key')
                if set(node.attrib) != {'key'} or key not in values or key in seen or len(node) or not node.text:
                    raise MathProjectionError('Each source-math must contain exact translated text and one known, unique key.')
                seen.add(key)
                text = node.text
                left, right = 0, len(text)
                while left < right and is_whitespace(text[left]):
                    left += 1
                while right > left and is_whitespace(text[right-1]):
                    right -= 1
                if left == right:
                    raise MathProjectionError('A source formula must have a nonempty translated value.')
                bindings.append(Binding(start + left, start + right, values[key], text[left:right]))
        walk(block)
        return sorted(bindings, key=lambda item: item.start)

    for block in root:
        bindings = block_spans(block)
        if bindings:
            try:
                identifier = int(block.get('id', ''))
            except ValueError as error:
                raise MathProjectionError('Keep the required block ID around source formulas.') from error
            result[identifier] = bindings
    if seen != values.keys():
        raise MathProjectionError(f'Missing formula source identities: {sorted(values.keys()-seen)}. '
                                  'Wrap each corresponding translated span in source-math with its source key exactly once.')
    return result


def strip_bindings(root: Element) -> Element:
    root = deepcopy(root)
    def walk(parent):
        previous = None
        for child in list(parent):
            walk(child)
            if child.tag == BINDING_TAG:
                text = (child.text or '') + (child.tail or '')
                if previous is None:
                    parent.text = (parent.text or '') + text
                else:
                    previous.tail = (previous.tail or '') + text
                parent.remove(child)
            else:
                previous = child
    walk(root)
    return root


def _node(source, text, factory):
    matches = CanonicalMath.matches(text)
    delimited = len(matches) == 1 and matches[0].group() == text
    content = formula_content(text)[0] if delimited else text
    original = formula_content(source.latex)[0]
    if unchanged_formula(original, content):
        node = deepcopy(source.element)
    else:
        value = text if delimited else '\\(' + literal_percent(content) + '\\)'
        node = factory(value)
        node.attrib = {**source.element.attrib, **node.attrib}
    node.tail = None
    return node


def _events(text, bindings, factory):
    events, used = [], set()
    for match in CanonicalMath.matches(text):
        selected = [(i, binding) for i, binding in enumerate(bindings)
                    if binding.start < match.end() and match.start() < binding.end]
        if not selected:
            continue
        if len(selected) == 1 and (selected[0][1].start, selected[0][1].end) == match.span():
            index, binding = selected[0]
            events.append((*match.span(), [_node(binding.source, binding.text, factory)]))
            used.add(index)
            continue
        content, inline = formula_content(match.group())
        value = match.group()
        opening_width = (2 if value.startswith('$$') else 1) if value.startswith('$') else len(value)-len(value.lstrip('\\'))+1
        start = match.start() + opening_width
        end = start + len(content)
        cursor, pieces = start, []
        opening, closing = ('\\(', '\\)') if inline else ('\\[', '\\]')
        for index, binding in selected:
            if not start <= binding.start < binding.end <= end:
                raise MathProjectionError(f'A source-math span must cover a complete formula or a complete part inside it: {text!r}, {binding!r}, match={match.span()}.')
            if cursor < binding.start:
                residual = text[cursor:binding.start]
                pieces.append(residual if not residual.strip() else factory(opening + residual + closing))
            pieces.append(_node(binding.source, binding.text, factory))
            cursor = binding.end
            used.add(index)
        if cursor < end:
            residual = text[cursor:end]
            pieces.append(residual if not residual.strip() else factory(opening + residual + closing))
        events.append((*match.span(), pieces))
    for index, binding in enumerate(bindings):
        if index not in used:
            events.append((binding.start, binding.end, [_node(binding.source, binding.text, factory)]))
    events.sort(key=lambda event: event[0])
    if any(left[1] > right[0] for left, right in zip(events, events[1:])):
        raise MathProjectionError('Formula identity spans cannot overlap.')
    return events


def project_xml_math(mappings, response, math, factory):
    spans = binding_spans(response, math)
    if not spans:
        return None
    # candidate_mappings follows source block order; XML IDs select its spans.
    block_ids = [int(node.get('id', '0')) for node in response if node.get('id') is not None]
    block_ids.sort()
    projected = []
    for identifier, mapping in zip(block_ids, mappings, strict=True):
        if mapping is None:
            projected.append(None)
            continue
        block, fields = mapping
        text = ''.join(field.text for field in fields)
        events = _events(text, spans.get(identifier, []), factory)
        output, offset = [], 0
        for field in fields:
            end = offset + len(field.text)
            stack = [node for node in field.parent_stack
                     if not (node.tag == 'expression' and is_inline_element(node))]
            cursor = offset
            for start, finish, pieces in events:
                if finish <= offset or start >= end:
                    continue
                if cursor < start:
                    output.append(replace(field, text=text[cursor:start], parent_stack=stack,
                                          block_depth=find_block_depth(stack)))
                if offset <= start:
                    for piece in pieces:
                        if isinstance(piece, str):
                            output.append(replace(field, text=piece, parent_stack=stack,
                                                  block_depth=find_block_depth(stack)))
                        else:
                            for part in search_text_segments(piece, normalize_whitespace=False):
                                parents = stack + part.parent_stack
                                output.append(replace(part, parent_stack=parents, block_depth=find_block_depth(parents),
                                                      origins=field.origins, progress_blocks=field.progress_blocks))
                cursor = min(end, finish)
            if cursor < end or not field.text:
                output.append(replace(field, text=text[cursor:end], parent_stack=stack,
                                      block_depth=find_block_depth(stack)))
            offset = end
        roots = list(combine_text_segments(output))
        if len(roots) > 1:
            raise MathProjectionError('Formula projection must retain its owning block.')
        root = roots[0][0] if roots else Element(block.tag, block.attrib)
        result = list(search_text_segments(root))
        projected.append((block, result or [TextSegment('', [root], 0, 0, 1, TextPosition.TEXT)]))
    return projected
