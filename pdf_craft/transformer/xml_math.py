"""Read formula identities directly from accepted XML fill, then project typed nodes."""
from copy import deepcopy
from dataclasses import dataclass, replace
import re
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
        def walk(node, inside_binding=False):
            nonlocal offset
            start = offset
            binding = node.tag == BINDING_TAG
            keys = node.get('keys', '').split() if 'keys' in node.attrib else [node.get('key')]
            location = f'XML block id={block.get("id")}, source-math identities={keys}'
            if binding:
                if inside_binding:
                    raise MathProjectionError(f'{location}: source identity spans overlap. Use adjacent source-math tags, not nested identities.')
                if set(node.attrib) not in ({'key'}, {'keys'}) or not keys:
                    raise MathProjectionError(f'{location}: use exactly key="sN" or ordered keys="sN sM".')
                for key in keys:
                    if key not in values:
                        raise MathProjectionError(f'{location}: unknown source identity {key!r}. Required keys: {list(values)}. '
                                                  'Unwrap an invented annotation while retaining its exact text and formatting.')
                    if key in seen:
                        raise MathProjectionError(f'{location}: duplicate source identity {key}. Keep its semantic occurrence once; every other source needs its own key.')
                    seen.add(key)
            offset += len(node.text or '')
            for child in node:
                walk(child, inside_binding or binding)
                offset += len(child.tail or '')
            if binding:
                # An annotation can wrap existing formatting. Unwrapping it
                # retains those exact nodes for ordinary template validation.
                text = ''.join(node.itertext())
                left, right = 0, len(text)
                while left < right and is_whitespace(text[left]):
                    left += 1
                while right > left and is_whitespace(text[right-1]):
                    right -= 1
                if left == right:
                    raise MathProjectionError(f'{location}: empty translated value. Wrap the corresponding existing artist text; do not copy source padding.')
                if len(keys) == 1:
                    bindings.append(Binding(start + left, start + right, values[keys[0]], text[left:right]))
                else:
                    for begin, end, key in _partition_unchanged(text[left:right], keys, values):
                        bindings.append(Binding(start+left+begin, start+left+end, values[key], text[left+begin:left+end]))
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
        missing = [key for key in values if key not in seen]
        details = [math.describe(key) for key in missing[:6]]
        raise MathProjectionError(f'Missing formula source identities: {sorted(missing)}. '
                                  + ' '.join(details) +
                                  ' Bind each missing key to its translated semantic span once. '
                                  'If the artist merged source formulas, write ONE literal complete artist formula with adjacent source-math annotations INSIDE its delimiters; '
                                  'or wrap the ONE existing complete formula reference in <source-math keys="sN sM"> with your explicitly ordered source identities. '
                                  'Keep the other required expression containers empty. Do not add source text to the artist translation.')
    return result



def _partition_unchanged(text, keys, values):
    """Partition syntax only AFTER the model explicitly provides ordered identities.

    Never select source IDs, search prose for matching values or guess semantic
    correspondence. A changed/nonliteral combined span requires individual XML
    annotations; only a unique unchanged syntax concatenation is automatic.
    """
    matches = CanonicalMath.matches(text)
    if matches:
        if len(matches) != 1 or matches[0].group() != text:
            raise MathProjectionError(f'Combined source identities {keys} require one complete artist formula; otherwise use individual annotations.')
        content, _ = formula_content(text)
        opening = (2 if text.startswith('$$') else 1) if text.startswith('$') else len(text)-len(text.lstrip('\\'))+1
    else:
        content, opening = text, 0
    originals = [formula_content(values[key].latex)[0] for key in keys]
    tokenize = lambda value: list(re.finditer(r'\\[A-Za-z]+|\\.|[^\s]', value))
    tokens = tokenize(content)
    source_tokens = [[token.group() for token in tokenize(value)] for value in originals]
    if (not all(source_tokens) or [token.group() for token in tokens] != [token for group in source_tokens for token in group]
            or not any(unchanged_formula(separator.join(originals), content) for separator in ('', ' '))):
        raise MathProjectionError(f'Combined identities {keys} do not have a unique unchanged syntax partition. '
                                  'Use adjacent single-key source-math annotations around the exact translated subspans, inside the original delimiters; changed values are allowed.')
    cursor = 0
    for key, group in zip(keys, source_tokens, strict=True):
        end = cursor+len(group)
        yield opening+tokens[cursor].start(), opening+tokens[end-1].end(), key
        cursor = end


def _format_shape(node, math_tags):
    """Formula projection cannot erase or invent surrounding rich structure."""
    return (node.tag, tuple(sorted(node.attrib.items())), tuple(
        _format_shape(child, math_tags) for child in node if child.tag not in math_tags))


def strip_bindings(root: Element) -> Element:
    root = deepcopy(root)
    def walk(parent):
        previous = None
        for child in list(parent):
            walk(child)
            if child.tag == BINDING_TAG:
                if previous is None:
                    parent.text = (parent.text or '') + (child.text or '')
                else:
                    previous.tail = (previous.tail or '') + (child.text or '')
                index = list(parent).index(child)
                for nested in list(child):
                    child.remove(nested)
                    parent.insert(index, nested)
                    previous = nested
                    index += 1
                if previous is None:
                    parent.text = (parent.text or '') + (child.tail or '')
                else:
                    previous.tail = (previous.tail or '') + (child.tail or '')
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
        original_roots = list(combine_text_segments(fields))
        original_root = original_roots[0][0] if original_roots else Element(block.tag, block.attrib)
        math_tags = {'expression', *(source.element.tag for source in math.values.values())}
        if _format_shape(root, math_tags) != _format_shape(original_root, math_tags):
            raise MathProjectionError(f'Formula annotation in XML block id={identifier} spans incompatible formatting scopes. '
                                      'Keep every surrounding rich tag. Put source-math inside its intended formatting scope, not around unrelated styled text.')
        result = list(search_text_segments(root))
        projected.append((block, result or [TextSegment('', [root], 0, 0, 1, TextPosition.TEXT)]))
    return projected
