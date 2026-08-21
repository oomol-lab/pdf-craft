from collections.abc import Callable, Generator, Iterable, Iterator
from typing import TypeVar
from xml.etree.ElementTree import Element

from resource_segmentation import Group, Resource, Segment, split
from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import InlineSegment, TextSegment, search_inline_segments, search_text_segments
from .callbacks import Callbacks
from .concurrency import run_concurrency
from .score import ScoreSegment, expand_to_score_segments, truncate_score_segment

_PAGE_INCISION = 0
_BLOCK_INCISION = 1
_T = TypeVar("_T")

_ResourcePayload = tuple[InlineSegment, list[ScoreSegment]]


InlineSegmentMapping = tuple[Element, list[TextSegment]]
InlineSegmentGroupMap = Callable[[list[InlineSegment]], list[InlineSegmentMapping | None]]


class XMLStreamMapper:
    def __init__(self, encoding: Encoding, max_group_score: int) -> None:
        self._encoding: Encoding = encoding
        self._max_group_score: int = max_group_score

    def map_stream(
        self,
        elements: Iterator[Element],
        callbacks: Callbacks,
        map: InlineSegmentGroupMap,
        concurrency: int,
    ) -> Generator[tuple[Element, list[InlineSegmentMapping]], None, None]:
        current_element: Element | None = None
        mapping_buffer: list[InlineSegmentMapping] = []

        def execute(group: Group[_ResourcePayload]):
            head, body, tail = self._truncate_and_transform_group(group)
            head = [segment.clone() for segment in head]
            tail = [segment.clone() for segment in tail]
            target_body = map(head + body + tail)[len(head) : len(head) + len(body)]
            return zip(body, target_body, strict=False)

        for mapping_pairs in run_concurrency(
            parameters=self._split_into_serial_groups(elements, callbacks),
            execute=execute,
            concurrency=concurrency,
        ):
            for origin, target in mapping_pairs:
                origin_element = origin.head.root
                if current_element is None:
                    current_element = origin_element

                if id(current_element) != id(origin_element):
                    yield current_element, mapping_buffer
                    current_element = origin_element
                    mapping_buffer = []

                if target:
                    block_element, text_segments = target
                    block_element = callbacks.interrupt_block_element(block_element)
                    text_segments = list(callbacks.interrupt_translated_text_segments(text_segments))
                    if text_segments:
                        mapping_buffer.append((block_element, text_segments))

        if current_element is not None:
            yield current_element, mapping_buffer

    def _split_into_serial_groups(self, elements: Iterable[Element], callbacks: Callbacks):
        def generate():
            for element in elements:
                yield from split(
                    max_segment_count=self._max_group_score,
                    border_incision=_PAGE_INCISION,
                    resources=self._expand_to_resources(element, callbacks),
                )

        generator = generate()
        group = next(generator, None)
        if group is None:
            return

        # head + body * N (without tail)
        sum_count = group.head_remain_count + sum(x.count for x in self._expand_resource_segments(group.body))

        while True:
            next_group = next(generator, None)
            if next_group is None:
                break

            next_sum_body_count = sum(x.count for x in self._expand_resource_segments(next_group.body))
            next_sum_count = sum_count + next_sum_body_count

            if next_sum_count + next_group.tail_remain_count > self._max_group_score:
                yield group
                group = next_group
                sum_count = group.head_remain_count + next_sum_body_count
            else:
                group.body.extend(next_group.body)
                group.tail = next_group.tail
                group.tail_remain_count = next_group.tail_remain_count
                sum_count = next_sum_count

        yield group

    def _truncate_and_transform_group(
        self, group: Group[_ResourcePayload]
    ) -> tuple[list[InlineSegment], list[InlineSegment], list[InlineSegment]]:
        head = self._truncate_group_gap(
            gap=group.head,
            remain_head=False,
            remain_score=group.head_remain_count,
        )
        body = self._expand_inline_segments(group.body)
        tail = self._truncate_group_gap(
            gap=group.tail,
            remain_head=True,
            remain_score=group.tail_remain_count,
        )
        return (
            [r.payload[0] for r in head],
            [p[0] for p in body],
            [r.payload[0] for r in tail],
        )

    def _expand_to_resources(self, element: Element, callbacks: Callbacks):
        def expand(element: Element):
            text_segments = search_text_segments(element)
            text_segments = callbacks.interrupt_source_text_segments(text_segments)
            yield from search_inline_segments(text_segments)

        inline_segment_generator = expand(element)
        start_incision = _PAGE_INCISION
        inline_segment = next(inline_segment_generator, None)
        if inline_segment is None:
            return

        while True:
            next_inline_segment = next(inline_segment_generator, None)
            if next_inline_segment is None:
                break

            if next_inline_segment.head.root is inline_segment.tail.root:
                end_incision = _BLOCK_INCISION
            else:
                end_incision = _PAGE_INCISION

            yield self._transform_to_resource(
                inline_segment=inline_segment,
                start_incision=start_incision,
                end_incision=end_incision,
            )
            inline_segment = next_inline_segment
            start_incision = end_incision

        yield self._transform_to_resource(
            inline_segment=inline_segment,
            start_incision=start_incision,
            end_incision=_PAGE_INCISION,
        )

    def _transform_to_resource(
        self,
        inline_segment: InlineSegment,
        start_incision: int,
        end_incision: int,
    ) -> Resource[_ResourcePayload]:
        source_segments = list(
            expand_to_score_segments(
                encoding=self._encoding,
                inline_segment=inline_segment,
            )
        )
        return Resource(
            count=sum(segment.score for segment in source_segments),
            start_incision=start_incision,
            end_incision=end_incision,
            payload=(inline_segment, source_segments),
        )

    def _expand_inline_segments(self, items: list[Resource[_ResourcePayload] | Segment[_ResourcePayload]]):
        for resource in self._expand_resource_segments(items):
            yield resource.payload

    def _expand_resource_segments(self, items: list[Resource[_ResourcePayload] | Segment[_ResourcePayload]]):
        for item in items:
            if isinstance(item, Resource):
                yield item
            elif isinstance(item, Segment):
                yield from item.resources

    def _truncate_group_gap(
        self,
        gap: list[Resource[_ResourcePayload] | Segment[_ResourcePayload]],
        remain_head: bool,
        remain_score: int,
    ):
        def expand_resource_segments(items: list[Resource[_ResourcePayload] | Segment[_ResourcePayload]]):
            for item in items:
                if isinstance(item, Resource):
                    yield item
                elif isinstance(item, Segment):
                    yield from item.resources

        resources, remain_score = _truncate_items(
            items=expand_resource_segments(gap),
            score=lambda resource: resource.count,
            remain_head=remain_head,
            remain_score=remain_score,
        )
        if remain_score > 0:
            resource = resources.pop() if remain_head else resources.pop(0)
            inline_segment, score_segments = resource.payload
            score_segments, remain_score = _truncate_items(
                items=score_segments,
                score=lambda score_segment: score_segment.score,
                remain_head=remain_head,
                remain_score=remain_score,
            )
            if remain_score > 0:
                score_segment = score_segments.pop() if remain_head else score_segments.pop(0)
                score_segment = truncate_score_segment(
                    score_segment=score_segment,
                    encoding=self._encoding,
                    remain_head=remain_head,
                    remain_score=remain_score,
                )
                if score_segment is not None:
                    if remain_head:
                        score_segments.append(score_segment)
                    else:
                        score_segments.insert(0, score_segment)

                inline_segment = next(
                    search_inline_segments(s.text_segment for s in score_segments),
                    None,
                )

            if inline_segment is not None:
                resource = Resource(
                    count=sum(s.score for s in score_segments),
                    start_incision=resource.start_incision,
                    end_incision=resource.end_incision,
                    payload=(inline_segment, score_segments),
                )
                if remain_head:
                    resources.append(resource)
                else:
                    resources.insert(0, resource)

        return resources


def _truncate_items(items: Iterable[_T], score: Callable[[_T], int], remain_head: bool, remain_score: int):
    truncated_items = list(items)
    if not truncated_items:
        return truncated_items, 0

    if not remain_head:
        truncated_items.reverse()

    truncated_index: int | None = None
    for i, item in enumerate(truncated_items):
        item_score = score(item)
        remain_score -= item_score
        if remain_score <= 0:
            truncated_index = i
            break

    if truncated_index is not None:
        while len(truncated_items) > truncated_index + 1:
            truncated_items.pop()

    if truncated_items and remain_score < 0:
        remain_score = score(truncated_items[-1]) + remain_score
    else:
        remain_score = 0

    if not remain_head:
        truncated_items.reverse()

    return truncated_items, remain_score
