from collections.abc import Generator, Iterable
from typing import cast
from xml.etree.ElementTree import Element, tostring

from bs4 import BeautifulSoup
from mathml2latex.mathml import process_mathml  # pyright: ignore[reportMissingImports]

from pdf_craft.transformer.xml_translator.segment import TextSegment, combine_text_segments, find_block_depth
from ..utils import ensure_list, normalize_whitespace
from pdf_craft.transformer.xml_translator.xml import DISPLAY_ATTRIBUTE, clone_element, is_inline_element

_ID_KEY = "__XML_INTERRUPTER_ID"
_MATH_TAG = "math"
_EXPRESSION_TAG = "expression"


class XMLInterrupter:
    def __init__(self) -> None:
        self._next_id: int = 1
        self._last_interrupted_id: str | None = None
        self._placeholder2interrupted: dict[int, Element] = {}
        self._raw_text_segments: dict[str, list[TextSegment]] = {}

    def interrupt_source_text_segments(
        self, text_segments: Iterable[TextSegment]
    ) -> Generator[TextSegment, None, None]:
        for text_segment in text_segments:
            yield from self._expand_source_text_segment(text_segment)

        if self._last_interrupted_id is not None:
            merged_text_segment = self._pop_and_merge_from_buffered(self._last_interrupted_id)
            if merged_text_segment:
                yield merged_text_segment

    def interrupt_translated_text_segments(
        self, text_segments: Iterable[TextSegment]
    ) -> Generator[TextSegment, None, None]:
        for text_segment in text_segments:
            yield from self._expand_translated_text_segment(text_segment)

    def interrupt_block_element(self, element: Element) -> Element:
        interrupted_element = self._placeholder2interrupted.pop(id(element), None)
        if interrupted_element is None:
            element.attrib.pop(_ID_KEY, None)
            return element
        else:
            interrupted_element.attrib.pop(_ID_KEY, None)
            return interrupted_element

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
        text_segments = self._raw_text_segments.get(interrupted_id, None)
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
            interrupted_display = interrupted_element.get(DISPLAY_ATTRIBUTE, None)
            if interrupted_display is not None:
                placeholder_element.set(DISPLAY_ATTRIBUTE, interrupted_display)

            raw_parent_stack = text_segment.parent_stack[:interrupted_index]
            parent_stack = raw_parent_stack + [placeholder_element]
            merged_text_segment = TextSegment(
                text=self._render_latex(text_segments),
                parent_stack=parent_stack,
                left_common_depth=text_segments[0].left_common_depth,
                right_common_depth=text_segments[-1].right_common_depth,
                block_depth=find_block_depth(parent_stack),
                position=text_segments[0].position,
            )
            self._placeholder2interrupted[id(placeholder_element)] = interrupted_element

            # TODO: 比较难搞，先关了再说
            # parent_element: Element | None = None
            # if interrupted_index > 0:
            #     parent_element = text_segment.parent_stack[interrupted_index - 1]

            # if (
            #     not self._is_inline_math(interrupted_element)
            #     or parent_element is None
            #     or self._has_no_math_texts(parent_element)
            # ):
            #     # 区块级公式不必重复出现，出现时突兀。但行内公式穿插在译文中更有利于读者阅读顺畅。
            #     self._raw_text_segments.pop(interrupted_id, None)
            # else:
            #     for text_segment in text_segments:
            #         # 原始栈退光，仅留下相对 interrupted 元素的栈，这种格式与 translated 要求一致
            #         text_segment.left_common_depth = max(0, text_segment.left_common_depth - interrupted_index)
            #         text_segment.right_common_depth = max(0, text_segment.right_common_depth - interrupted_index)
            #         text_segment.block_depth = 1
            #         text_segment.parent_stack = text_segment.parent_stack[interrupted_index:]
            for text_segment in text_segments:
                # 原始栈退光，仅留下相对 interrupted 元素的栈，这种格式与 translated 要求一致
                text_segment.left_common_depth = max(0, text_segment.left_common_depth - interrupted_index)
                text_segment.right_common_depth = max(0, text_segment.right_common_depth - interrupted_index)
                text_segment.parent_stack = text_segment.parent_stack[interrupted_index:]
                text_segment.block_depth = find_block_depth(text_segment.parent_stack)

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
            latex = normalize_whitespace(latex).strip()
        else:
            latex = normalize_whitespace(latex).strip()
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
            yield raw_text_segment
