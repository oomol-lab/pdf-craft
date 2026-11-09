"""Project accepted math using original source occurrences, never generated values.

Source expression nodes are allocation containers. They are transparent when
the canonical text declares mathematical boundaries: replacing their contents
with an unrelated source formula would invalidate the text just accepted.
"""
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass, replace
import re
import unicodedata
from xml.etree.ElementTree import Element

from .canonical_math import CanonicalMath
from .whitespace import WhiteSpace, normalize
from .xml_translator.segment import TextPosition, TextSegment, combine_text_segments, find_block_depth, search_text_segments
from .xml_translator.xml import is_inline_element

MathMapping = tuple[Element, list[TextSegment]]


class MathProjectionError(ValueError):
    """Canonical math cannot be represented without changing accepted content."""

    attempts: int = 1


@dataclass(frozen=True)
class SourceMath:
    latex: str
    element: Element
    slot: Element | None = None
    context: bool = False


def formula_content(value: str) -> tuple[str, bool]:
    """Read the delimiters recognized by the canonical transport protocol."""
    if value.startswith('$$'):
        return value[2:-2], False
    if value.startswith('$'):
        return value[1:-1], True
    match = re.fullmatch(r'\\+([\(\[])(.*?)\\+[)\]]', value, re.DOTALL)
    if match is None:
        raise MathProjectionError('Invalid canonical mathematical delimiters.')
    return match[2], match[1] == '('


def _normalized(value: str) -> str:
    return unicodedata.normalize('NFD', normalize(value, WhiteSpace.NORMAL).strip())


def project_canonical_math(
    mappings: list[MathMapping | None], canonical: str, bindings: list[list[SourceMath | str]],
    residual_factory: Callable[[str], Element] | None = None,
) -> list[MathMapping | None] | None:
    values = list(CanonicalMath(canonical).values.values())
    if not values:
        return None
    segments = [segment for mapping in mappings if mapping for segment in mapping[1]]
    if any(segment.block_parent.tag == 'expression' for segment in segments):
        # Standalone equations are immutable format-owned blocks, not inline
        # allocation containers. Their adapter retains the original block.
        return None
    text = ''.join(segment.text for segment in segments)
    matches = CanonicalMath.matches(text)
    if len(matches) != len(values) or any(
        _normalized(match.group()) != _normalized(value) for match, value in zip(matches, values)
    ):
        raise MathProjectionError('Canonical mathematical boundaries differ from the accepted formula table.')
    if len(bindings) != len(values):
        raise MathProjectionError('Missing source identities for canonical mathematical boundaries.')
    projected: list[MathMapping | None] = []
    offset = 0
    for mapping in mappings:
        if mapping is None:
            projected.append(None)
            continue
        block, fields = mapping
        output: list[TextSegment] = []
        for field in fields:
            end = offset + len(field.text)
            stack = [node for node in field.parent_stack
                     if not (node.tag == 'expression' and is_inline_element(node))]
            cursor = offset
            for match, binding in zip(matches, bindings):
                if match.end() <= offset or match.start() >= end:
                    continue
                if cursor < match.start():
                    output.append(replace(field, text=text[cursor:match.start()], parent_stack=stack,
                                          block_depth=find_block_depth(stack)))
                if not binding:
                    # The engineer explicitly preserved additional artist math
                    # without inventing a source identity or rich source node.
                    output.append(replace(field, text=text[max(offset, match.start()):min(end, match.end())],
                                          parent_stack=stack, block_depth=find_block_depth(stack)))
                elif offset <= match.start():
                    for source in binding:
                        if isinstance(source, str):
                            if not source.strip(' \t\r\n'):
                                output.append(replace(field, text=source, parent_stack=stack,
                                                      block_depth=find_block_depth(stack)))
                                continue
                            if residual_factory is None:
                                raise MathProjectionError('A format adapter must materialize residual mathematics.')
                            node = residual_factory(source)
                        else:
                            node = deepcopy(source.element)
                        node.tail = None
                        for part in search_text_segments(node, normalize_whitespace=False):
                            parents = stack + part.parent_stack
                            output.append(replace(part, parent_stack=parents, block_depth=find_block_depth(parents),
                                                  origins=field.origins, progress_blocks=field.progress_blocks))
                cursor = min(end, match.end())
            if cursor < end or not field.text:
                output.append(replace(field, text=text[cursor:end], parent_stack=stack,
                                      block_depth=find_block_depth(stack)))
            offset = end
        roots = list(combine_text_segments(output))
        if not roots:
            # An entirely empty, accepted allocation is still a successful
            # mapping; it must not fall back to its untranslated source.
            root = Element(block.tag, block.attrib)
        elif len(roots) == 1:
            root = roots[0][0]
        else:
            raise MathProjectionError('A mathematical projection must retain its owning block.')
        # These fields already passed canonical whitespace validation. Formula
        # payload is format data now; a second CSS pass would change TeX text.
        result = list(search_text_segments(root, normalize_whitespace=False))
        if not result:
            # This sentinel occupies the new root's text, even when the
            # accepted allocation has no fields or consumed an entire tail.
            result = [TextSegment('', [root], 0, 0, 1, TextPosition.TEXT)]
        projected.append((block, result))
    return projected
