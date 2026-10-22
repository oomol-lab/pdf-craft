from collections.abc import Callable, Iterable
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.segment import TextSegment


@dataclass
class Callbacks:
    interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]]
    interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]]
    interrupt_block_element: Callable[[Element], Element]


def warp_callbacks(
    interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None,
    interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None,
    interrupt_block_element: Callable[[Element], Element] | None,
) -> Callbacks:
    return Callbacks(
        interrupt_source_text_segments=interrupt_source_text_segments or (lambda x: x),
        interrupt_translated_text_segments=interrupt_translated_text_segments or (lambda x: x),
        interrupt_block_element=interrupt_block_element or (lambda x: x),
    )
