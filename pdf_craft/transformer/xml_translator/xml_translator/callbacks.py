from collections.abc import Callable, Iterable
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.segment import TextSegment


@dataclass
class FillFailedEvent:
    error_message: str
    retried_count: int
    over_maximum_retries: bool


@dataclass
class Callbacks:
    interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]]
    interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]]
    interrupt_block_element: Callable[[Element], Element]
    on_fill_failed: Callable[[FillFailedEvent], None]


def warp_callbacks(
    interrupt_source_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None,
    interrupt_translated_text_segments: Callable[[Iterable[TextSegment]], Iterable[TextSegment]] | None,
    interrupt_block_element: Callable[[Element], Element] | None,
    on_fill_failed: Callable[[FillFailedEvent], None] | None,
) -> Callbacks:
    return Callbacks(
        interrupt_source_text_segments=interrupt_source_text_segments or (lambda x: x),
        interrupt_translated_text_segments=interrupt_translated_text_segments or (lambda x: x),
        interrupt_block_element=interrupt_block_element or (lambda x: x),
        on_fill_failed=on_fill_failed or (lambda event: None),
    )
