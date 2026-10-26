from collections.abc import AsyncIterable, AsyncGenerator, Awaitable, Callable, Iterable, Iterator
from contextlib import aclosing
from dataclasses import dataclass
from itertools import chain
from typing import TypeVar
from xml.etree.ElementTree import Element

from resource_segmentation import Group, Resource, Segment, split
from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import InlineSegment, TextSegment, search_inline_segments, search_text_segments
from pdf_craft.runtime import invoke_callback
from .callbacks import Callbacks
from .concurrency import run_async_parameters, run_concurrency_async
from .source_store import source_identity, rebind, NODE_KEY, ROOT_KEY, unit_owner, unit_key
from .score import ScoreSegment, expand_to_score_segments, truncate_score_segment

_PAGE_INCISION = 0
_BLOCK_INCISION = 1
_T = TypeVar("_T")

_ResourcePayload = tuple[InlineSegment, list[ScoreSegment]]


InlineSegmentMapping = tuple[Element, list[TextSegment]]
AsyncInlineSegmentGroupMap = Callable[
    [list[InlineSegment]], Awaitable[list[InlineSegmentMapping | None]]
]


@dataclass
class _EmptyElement:
    """A consumed root with no resources, not a model translation group."""
    root: Element


class XMLStreamMapper:
    def __init__(self, encoding: Encoding, max_group_score: int) -> None:
        self._encoding: Encoding = encoding
        self._max_group_score: int = max_group_score

    async def map_stream_async(
        self,
        elements: Iterator[Element] | AsyncIterable[Element],
        callbacks: Callbacks,
        map: AsyncInlineSegmentGroupMap,
        window: int,
        on_mapping: Callable[[tuple[InlineSegment, list[TextSegment]]], object] | None = None,
        track_progress: bool = False,
        on_empty: Callable[[Element], object] | None = None,
    ) -> AsyncGenerator[tuple[Element, list[InlineSegmentMapping]], None]:
        current_element: Element | None = None
        mapping_buffer: list[InlineSegmentMapping] = []
        notifications = []

        async def accepted(root, mappings):
            rejected = {id(child) for node in root.iter() if node.get('__pdf_craft_missing') == '1'
                        for child in node.iter()}
            result = [(block, texts) for block, texts in mappings if id(block) not in rejected]
            if on_mapping is not None:
                for origin, texts, block in notifications:
                    if id(block) not in rejected:
                        await invoke_callback(on_mapping, (origin, texts))
            notifications.clear()
            return result


        async def execute(group: Group[_ResourcePayload] | _EmptyElement):
            if isinstance(group, _EmptyElement):
                await invoke_callback(on_empty, group.root)
                return ()
            head, body, tail = self._truncate_and_transform_group(group)
            head = [segment.clone() for segment in head]
            tail = [segment.clone() for segment in tail]
            for segment in head + tail:
                setattr(segment, "_translation_context", True)
            target_body = (await map(head + body + tail))[len(head) : len(head) + len(body)]
            return zip(body, target_body, strict=False)

        if isinstance(elements, AsyncIterable):
            async def groups():
                try:
                    async for element in elements:
                        yield_groups = self._split_into_serial_groups(
                            (element,), callbacks, on_mapping is not None, track_progress, on_empty is not None,
                        )
                        for group in yield_groups:
                            yield group
                finally:
                    close = getattr(elements, "aclose", None)
                    if close is not None:
                        await close()
            results = run_async_parameters(groups(), execute, window, preserve_sources=False)
        else:
            results = run_concurrency_async(
                self._split_into_serial_groups(elements, callbacks, on_mapping is not None, track_progress, on_empty is not None),
                execute, window,
            )
        async with aclosing(results):
            async for mapping_pairs in results:
                for origin, target in mapping_pairs:
                    origin_element = origin.head.root
                    if current_element is None:
                        current_element = origin_element

                    if source_identity(current_element) != source_identity(origin_element):
                        assert current_element is not None
                        yield current_element, await accepted(current_element, mapping_buffer)
                        current_element = origin_element
                        mapping_buffer = []

                    owner = unit_owner(origin)
                    if isinstance(elements, AsyncIterable):
                        owner = rebind(current_element, origin, owner)
                    assert owner is not None
                    owner.set('__pdf_craft_unit', unit_key(owner))
                    if target is None:
                        owner.set('__pdf_craft_missing', '1')
                    if target:
                        assert current_element is not None
                        block_element, text_segments = target
                        if isinstance(elements, AsyncIterable):
                            block_element = rebind(current_element, origin, block_element)
                        if isinstance(elements, AsyncIterable):
                            by_key = {node.get(NODE_KEY): node for node in current_element.iter() if node.get(NODE_KEY) is not None}
                            for segment in text_segments:
                                segment.parent_stack = [by_key.get(parent.get(NODE_KEY), parent) for parent in segment.parent_stack]
                        assert block_element is not None
                        block_element = callbacks.interrupt_block_element(block_element)
                        text_segments = list(callbacks.interrupt_translated_text_segments(text_segments))
                        if text_segments:
                            if on_mapping is not None:
                                notifications.append((origin, text_segments, block_element))
                            mapping_buffer.append((block_element, text_segments))

        if current_element is not None:
            yield current_element, await accepted(current_element, mapping_buffer)

    def _split_into_serial_groups(self, elements: Iterable[Element], callbacks: Callbacks,
                                  track_origins: bool = False, track_progress: bool = False,
                                  include_empty: bool = False):
        def generate():
            for element in elements:
                resources = self._expand_to_resources(element, callbacks, track_origins, track_progress)
                first = next(resources, None)
                if first is None:
                    if include_empty:
                        yield _EmptyElement(element)
                    continue
                yield from split(
                    max_segment_count=self._max_group_score,
                    border_incision=_PAGE_INCISION,
                    resources=chain((first,), resources),
                )

        group = None
        sum_count = 0
        for next_group in generate():
            if isinstance(next_group, _EmptyElement):
                # Empty roots need settlement but do not interrupt batching of
                # the neighboring nonempty roots or introduce model requests.
                yield next_group
                continue
            next_sum_body_count = sum(x.count for x in self._expand_resource_segments(next_group.body))
            if group is None:
                group = next_group
                sum_count = group.head_remain_count + next_sum_body_count
                continue
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
        if group is not None:
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

    def _expand_to_resources(self, element: Element, callbacks: Callbacks, track_origins: bool = False, track_progress: bool = False):
        def expand(element: Element):
            # Weights belong to original blocks, before formula/context transport
            # projection. Never count temporary tokens or prompt context.
            from ...word_count import xml_block_word_counts
            original_blocks = {}
            if track_progress:
                for block, words in xml_block_word_counts(element):
                    original_blocks[id(block)] = original_blocks.get(id(block), 0) + words
            def source_segments():
                for text in search_text_segments(element, track_origins=track_origins):
                    if track_progress:
                        text.progress_blocks = (id(text.block_parent),)
                    yield text
            text_segments = callbacks.interrupt_source_text_segments(source_segments())
            for inline in search_inline_segments(text_segments):
                weight = 0
                for text in inline:
                    if text.progress_blocks:
                        for identity in text.progress_blocks:
                            weight += original_blocks.pop(identity, 0)
                        continue
                    if any(parent.tag in ('formula_context', 'translation-context') for parent in text.parent_stack):
                        continue
                    for parent in reversed(text.parent_stack):
                        identity = id(parent)
                        if identity in original_blocks:
                            weight += original_blocks.pop(identity)
                            break
                inline._progress_words = weight  # pylint: disable=protected-access
                yield inline

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

            for segment in inline_segment:
                for parent in segment.parent_stack:
                    if element.get(ROOT_KEY) is not None and parent.get(NODE_KEY) is None:
                        parent.set(NODE_KEY, 'new-' + str(id(parent)))
            yield self._transform_to_resource(
                inline_segment=inline_segment,
                start_incision=start_incision,
                end_incision=end_incision,
            )
            inline_segment = next_inline_segment
            start_incision = end_incision

        for segment in inline_segment:
            for parent in segment.parent_stack:
                if element.get(ROOT_KEY) is not None and parent.get(NODE_KEY) is None:
                    parent.set(NODE_KEY, 'new-' + str(id(parent)))
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
