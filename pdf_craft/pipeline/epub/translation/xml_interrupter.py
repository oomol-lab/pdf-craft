import re
from copy import deepcopy
from collections.abc import Generator, Iterable
from typing import cast
from xml.etree.ElementTree import Element, SubElement, tostring

from bs4 import BeautifulSoup
from mathml2latex.mathml import process_mathml  # pyright: ignore[reportMissingImports]

from pdf_craft.transformer.xml_translator.segment import TextSegment, combine_text_segments, find_block_depth
from pdf_craft.transformer.xml_translator.utils import ensure_list
from pdf_craft.transformer.xml_translator.xml import DISPLAY_ATTRIBUTE, clone_element, is_inline_element
from pdf_craft.transformer.formula_source import SourceMath
from pdf_craft.transformer.formula_payload import replace_formulas
from pdf_craft.formula.comparison import same_formula

_ID_KEY = "__XML_INTERRUPTER_ID"
_MATH_TAG = "math"
_EXPRESSION_TAG = "expression"


class XMLInterrupter:
    def __init__(self) -> None:
        self._next_id: int = 1
        self._last_interrupted_id: str | None = None
        self._placeholder2interrupted: dict[str, Element] = {}
        self._raw_text_segments: dict[str, list[TextSegment]] = {}
        self._latex_by_id: dict[str, str] = {}

    def prepare(self, root: Element) -> None:
        """MathML already declares inline/block display in the source schema."""

    def finish(self, root: Element) -> None:
        """No temporary projection identity belongs in the final XHTML."""
        for node in root.iter():
            node.attrib.pop(_ID_KEY, None)

    def source_formulas(self, segments: Iterable[TextSegment]) -> list[SourceMath]:
        identities = {node.get(_ID_KEY): node for segment in segments for node in segment.parent_stack
                      if _ID_KEY in node.attrib}
        sources = []
        for key, element in self._placeholder2interrupted.items():
            if key in identities and is_inline_element(element):
                node = clone_element(element)
                for child in node.iter():
                    child.attrib.pop(_ID_KEY, None)
                sources.append(SourceMath(self._latex_by_id[key], node, identities[key]))
        return sources

    def interrupt_source_text_segments(
        self, text_segments: Iterable[TextSegment]
    ) -> Generator[TextSegment, None, None]:
        for text_segment in text_segments:
            yield from self._expand_source_text_segment(text_segment)

        if self._last_interrupted_id is not None:
            merged_text_segment = self._pop_and_merge_from_buffered(self._last_interrupted_id)
            if merged_text_segment:
                yield merged_text_segment

    def materialize(self, root: Element) -> Element:
        def convert(node):
            value = ''.join(node.itertext())
            result = Element('math', {'xmlns': 'http://www.w3.org/1998/Math/MathML',
                                     'display': 'inline' if is_inline_element(node) else 'block'})
            key = node.get(_ID_KEY)
            source = self._placeholder2interrupted.get(key or '')
            if source is not None:
                result.attrib.update({k: v for k, v in source.attrib.items() if k != _ID_KEY and not k.startswith('__pdf_craft_')})
            if (source is not None and not self._literal_presentation(source)
                    and same_formula(self._latex_by_id[key or ''], value)):
                # The engineer selected this source node explicitly. Retain its
                # actual MathML presentation when the whole payload is unchanged.
                result = deepcopy(source)
                for child in result.iter():
                    child.attrib.pop(_ID_KEY, None)
                    for attribute in list(child.attrib):
                        if attribute.startswith('__pdf_craft_'):
                            child.attrib.pop(attribute)
                semantics = result.find('semantics')
                if semantics is None:
                    semantics = Element('semantics')
                    semantics.text, result.text = result.text, None
                    semantics[:] = list(result)
                    result[:] = [semantics]
                for annotation in list(semantics):
                    if annotation.tag == 'annotation' and annotation.get('encoding') == 'application/x-tex':
                        semantics.remove(annotation)
            else:
                semantics = SubElement(result, 'semantics')
                SubElement(semantics, 'mtext').text = value
            # Exact engineer payload is represented once; annotation is not a
            # second visible copy of the presentation children.
            SubElement(semantics, 'annotation', {'encoding': 'application/x-tex'}).text = value
            result.tail = node.tail
            return result
        return replace_formulas(root, convert)

    @staticmethod
    def _literal_presentation(root: Element) -> bool:
        presentation = root.find('semantics')
        children = list(root if presentation is None else presentation)
        children = [node for node in children if node.tag not in ('annotation', 'annotation-xml')]
        return len(children) == 1 and children[0].tag == 'mtext'

    def visible_text(self, root: Element) -> str:
        if root.tag == 'math':
            if self._literal_presentation(root):
                # Validate what a reader sees, not a hidden original string.
                presentation = root.find('semantics')
                return ''.join(node.text or '' for node in (root if presentation is None else presentation)
                               if node.tag == 'mtext')
            annotation = next((node for node in root.iter('annotation')
                               if node.get('encoding') == 'application/x-tex'), None)
            if annotation is not None:
                # Complex presentation is reused only from the explicitly
                # selected unchanged source node, never a lossy new conversion.
                return annotation.text or ''
        return (root.text or '') + ''.join(self.visible_text(child) + (child.tail or '') for child in root)

    def interrupt_translated_text_segments(self, text_segments: Iterable[TextSegment]) -> Iterable[TextSegment]:
        # Candidate materialization already ran inside the fill loop.
        return text_segments

    def interrupt_block_element(self, element: Element) -> Element:
        # This is source identity lookup, not content restoration. The mapper
        # rebinds it to the current document AFTER this callback.
        return self._placeholder2interrupted.get(element.get(_ID_KEY, ''), element)

    def _expand_source_text_segment(self, text_segment: TextSegment):
        interrupted_index = self._interrupted_index(text_segment)
        interrupted_id: str | None = None

        if interrupted_index is not None:
            interrupted_element = text_segment.parent_stack[interrupted_index]
            interrupted_id = interrupted_element.get(_ID_KEY)
            if interrupted_id is None:
                interrupted_id = str(self._next_id)
                interrupted_element.set(_ID_KEY, interrupted_id)
                self._next_id += 1
            text_segments = ensure_list(
                target=self._raw_text_segments,
                key=interrupted_id,
            )
            text_segments.append(text_segment)

        if self._last_interrupted_id is not None and interrupted_id != self._last_interrupted_id:
            merged_text_segment = self._pop_and_merge_from_buffered(self._last_interrupted_id)
            if merged_text_segment:
                yield merged_text_segment

        self._last_interrupted_id = interrupted_id

        if interrupted_index is None:
            yield text_segment

    def _pop_and_merge_from_buffered(self, interrupted_id: str) -> TextSegment | None:
        merged_text_segment: TextSegment | None = None
        text_segments = self._raw_text_segments.pop(interrupted_id, None)
        if text_segments:
            text_segment = text_segments[0]
            interrupted_index = cast(int, self._interrupted_index(text_segment))
            interrupted_element = text_segment.parent_stack[cast(int, interrupted_index)]
            placeholder_element = Element(
                _EXPRESSION_TAG,
                {
                    _ID_KEY: cast(str, interrupted_element.get(_ID_KEY)),
                },
            )
            # Resolve MathML's implicit inline default before projecting to an
            # otherwise unknown XML tag. Generic grouping must keep its owner.
            placeholder_element.set(DISPLAY_ATTRIBUTE,
                                    'inline' if is_inline_element(interrupted_element) else 'block')

            raw_parent_stack = text_segment.parent_stack[:interrupted_index]
            parent_stack = raw_parent_stack + [placeholder_element]
            merged_text_segment = TextSegment(
                text=self._render_latex(text_segments),
                parent_stack=parent_stack,
                left_common_depth=text_segments[0].left_common_depth,
                right_common_depth=text_segments[-1].right_common_depth,
                block_depth=find_block_depth(parent_stack),
                position=text_segments[0].position,
                origins=tuple(span for segment in text_segments for span in segment.origins),
                progress_blocks=tuple(dict.fromkeys(block for segment in text_segments for block in segment.progress_blocks)),
            )
            self._placeholder2interrupted[interrupted_id] = interrupted_element
            self._latex_by_id[interrupted_id] = merged_text_segment.text.strip()


        return merged_text_segment

    def _interrupted_index(self, text_segment: TextSegment) -> int | None:
        interrupted_index: int | None = None
        for i, parent_element in enumerate(text_segment.parent_stack):
            if parent_element.tag == _MATH_TAG:
                interrupted_index = i
                break
        return interrupted_index

    def _render_latex(self, text_segments: list[TextSegment]) -> str:
        math_element, _ = next(combine_text_segments(text_segments))
        while math_element.tag != _MATH_TAG:
            if len(math_element) == 0:
                return ""
            math_element = math_element[0]

        math_element = clone_element(math_element)
        math_element.attrib.pop(_ID_KEY, None)
        math_element.tail = None
        latex: str | None = None
        try:
            mathml_str = tostring(math_element, encoding="unicode")
            soup = BeautifulSoup(mathml_str, "html.parser")
            latex = process_mathml(soup)
        except Exception:
            pass

        if latex is None:
            latex = "".join(t.text for t in text_segments)
        # Math syntax is not ordinary document whitespace.
        latex = re.sub(r'[ \t\r\n]+', ' ', latex).strip()
        if is_inline_element(math_element):
            latex = f"${latex}$"
        else:
            latex = f"$${latex}$$"

        return f" {latex} "

    def _expand_translated_text_segment(self, text_segment: TextSegment):
        parent_element = text_segment.parent_stack[-1]
        interrupted_id = parent_element.attrib.pop(_ID_KEY, None)
        if interrupted_id is None:
            yield text_segment
            return

        if parent_element is text_segment.block_parent:
            # Block-level math， need to be hidden
            return

        raw_text_segments = self._raw_text_segments.pop(interrupted_id, None)
        if not raw_text_segments:
            yield text_segment
            return

        for raw_text_segment in raw_text_segments:
            text_basic_parent_stack = text_segment.parent_stack[:-1]
            raw_text_segment.block_parent.attrib.pop(_ID_KEY, None)
            raw_text_segment.parent_stack = text_basic_parent_stack + raw_text_segment.parent_stack
            raw_text_segment.block_depth = find_block_depth(raw_text_segment.parent_stack)
            yield raw_text_segment
