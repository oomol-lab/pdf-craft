"""Represent PCEX mathematics once in its actual narrative output slot.

Source identities are transport metadata. Only an accepted engineer answer is
converted to PCEX; no formula is frozen or copied into neighbouring paragraphs.
"""
import json
from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Iterable
from xml.etree.ElementTree import Element

from pdf_craft.expression import decode_expression_kind, to_markdown_string
from ..formula.comparison import same_formula
from .formula_payload import formula_payload, replace_formulas
from .fill_text import text_difference
from .xml_translator.segment import TextSegment
from .xml_translator.xml import DISPLAY_ATTRIBUTE, plain_text

_FORMULA_ID_KEY = '__PDF_CRAFT_CHAPTER_FORMULA_ID'
_INLINE_DISPLAY_KEY = '__PDF_CRAFT_INLINE_DISPLAY'


@dataclass(frozen=True)
class _Formula:
    element: Element
    latex: str
    block: bool


class ChapterFormulaInterrupter:
    """The same editable engineering representation for inline/display math."""
    def __init__(self):
        self._formula_by_id: dict[str, _Formula] = {}
        self._next_id = 0

    def prepare(self, root: Element) -> None:
        for index, element in enumerate(root.iter('display-formula')):
            element.set('__pdf_craft_formula_unit', f'formula:{index}')
        for element in root.iter('inline_expr'):
            element.set(_INLINE_DISPLAY_KEY, json.dumps(element.get(DISPLAY_ATTRIBUTE)))
            element.set(DISPLAY_ATTRIBUTE, 'inline')

    def finish(self, root: Element) -> None:
        for element in root.iter():
            element.attrib.pop(_FORMULA_ID_KEY, None)
            value = element.attrib.pop(_INLINE_DISPLAY_KEY, None)
            if value is not None:
                original = json.loads(value)
                if original is None:
                    element.attrib.pop(DISPLAY_ATTRIBUTE, None)
                else:
                    element.set(DISPLAY_ATTRIBUTE, original)

    def release(self, root: Element) -> None:
        nodes = {id(node) for node in root.iter()}
        self._formula_by_id = {key: value for key, value in self._formula_by_id.items()
                               if id(value.element) not in nodes}

    def interrupt_source_text_segments(self, segments: Iterable[TextSegment]) -> Iterable[TextSegment]:
        pending: list[TextSegment] = []
        current = None
        for segment in segments:
            found = self._formula_in(segment)
            if found != current and pending:
                yield self._project(current, pending)
                pending = []
            current = found
            if found is None:
                yield segment
            else:
                pending.append(segment)
        if pending:
            yield self._project(current, pending)

    @staticmethod
    def _formula_in(segment):
        for index, element in enumerate(segment.parent_stack):
            if element.tag == 'asset' and element.get('ref') == 'formula':
                content = element.find('content')
                if content is not None and content in segment.parent_stack[index+1:]:
                    return content, segment.parent_stack.index(content), True
            if element.tag == 'inline_expr' and element.get('kind') != 'text':
                return element, index, False
        return None

    def _project(self, found, segments):
        source, index, block = found
        first = segments[0]
        value = ''.join(segment.text for segment in segments)
        if block:
            latex = '$$' + value.strip() + '$$'
        else:
            latex = to_markdown_string(decode_expression_kind(source.get('kind', 'text')), value)
        self._next_id += 1
        key = str(self._next_id)
        self._formula_by_id[key] = _Formula(source, latex, block)
        token = Element('expression', {_FORMULA_ID_KEY: key, DISPLAY_ATTRIBUTE: 'block' if block else 'inline',
                                      **{k: v for k, v in source.attrib.items() if k.startswith('__pdf_craft_')}})
        return TextSegment(latex, [*first.parent_stack[:index], token],
                           first.left_common_depth, segments[-1].right_common_depth,
                           index + 1 if block else first.block_depth, first.position,
                           progress_blocks=tuple(dict.fromkeys(key for segment in segments for key in segment.progress_blocks)))

    def materialize(self, root: Element) -> Element:
        def convert(node):
            value = ''.join(node.itertext())
            source = self._formula_by_id.get(node.get(_FORMULA_ID_KEY, ''))
            if source is not None and source.block:
                result = Element('content', source.element.attrib)
                result.text = formula_payload(value)
            else:
                # Source reuse preserves the engineer's notation and wrappers,
                # not the original source kind. This runs after acceptance only.
                payload = formula_payload(value)
                kind = 'text'
                if payload != value.strip():
                    kind = value.strip()[:2] if value.strip().startswith(('\\(', '\\[')) else ('$$' if value.strip().startswith('$$') else '$')
                result = (deepcopy(source.element) if source is not None and same_formula(source.latex, value)
                          else Element('inline_expr'))
                result[:] = []
                result.set(DISPLAY_ATTRIBUTE, 'inline')
                result.set(_INLINE_DISPLAY_KEY, result.get(_INLINE_DISPLAY_KEY, json.dumps(None)))
                result.set('kind', kind)
                result.text = payload if kind != 'text' else value
                if source is not None and kind != 'text' and same_formula(source.latex, value):
                    original = formula_payload(source.latex)
                    represented = to_markdown_string(decode_expression_kind(kind), original)
                    # Reuse is optional. If its representation cannot be proved
                    # equal, keep the accepted engineer payload instead of
                    # manufacturing an engineering failure or another retry.
                    if text_difference(value, represented) is None:
                        result.text = original
            result.tail = node.tail
            return result
        return replace_formulas(root, convert)

    def delivery_source_text(self, root: Element) -> str:
        value = plain_text(root)
        source = self._formula_by_id.get(root.get(_FORMULA_ID_KEY, ''))
        return formula_payload(value) if source is not None and source.block else value

    def visible_text(self, root: Element) -> str:
        if root.tag == 'inline_expr' and root.get('kind') != 'text':
            return to_markdown_string(decode_expression_kind(root.get('kind', 'text')), plain_text(root))
        return (root.text or '') + ''.join(self.visible_text(child) + (child.tail or '') for child in root)

    def interrupt_translated_text_segments(self, segments: Iterable[TextSegment]) -> Iterable[TextSegment]:
        return segments

    def interrupt_block_element(self, element: Element) -> Element:
        source = self._formula_by_id.get(element.get(_FORMULA_ID_KEY, ''))
        return source.element if source is not None else element
