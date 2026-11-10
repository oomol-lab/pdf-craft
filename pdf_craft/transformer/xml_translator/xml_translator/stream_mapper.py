# pylint: disable=protected-access
from collections.abc import AsyncIterable, AsyncGenerator, Awaitable, Callable, Iterable, Iterator
from contextlib import aclosing, nullcontext
from collections import deque
from dataclasses import dataclass
from itertools import chain, islice
from typing import TypeVar
from xml.etree.ElementTree import Element

from resource_segmentation import Group, Resource, Segment, split
from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import InlineSegment, TextSegment, search_inline_segments, search_text_segments
from pdf_craft.runtime import invoke_callback
from pdf_craft._progress import OperationProgress
from ...request_batch import MAX_BATCH_UNITS
import asyncio
import time
from .callbacks import Callbacks
from .fill_diagnostics import FillDiagnostics
from .concurrency import run_async_parameters
from .source_store import source_identity, rebind, NODE_KEY, ROOT_KEY
from .score import ScoreSegment, expand_to_score_segments, truncate_score_segment

_PAGE_INCISION = 0
_BLOCK_INCISION = 1
_SOURCE_IDLE_TIMEOUT = 0.2
_SOURCE_READY_TIMEOUT = 1.0
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


@dataclass
class _Work:
    """One bounded group with private, read-only overlap slots."""
    segments: list[InlineSegment]
    primary: list[bool]


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
        progress: OperationProgress | None = None,
        on_empty: Callable[[Element], object] | None = None,
        *, ownership=None, ordered_roots=True, diagnostics: FillDiagnostics | None = None,
    ) -> AsyncGenerator[tuple[Element, list[InlineSegmentMapping]], None]:
        current_element: Element | None = None
        mapping_buffer: list[InlineSegmentMapping] = []
        notifications = deque()
        root_states = {}
        expected = {}
        completed = {}

        def expanded(root, count):
            if not ordered_roots:
                expected[source_identity(root)] = count

        def rejected(root):
            return {id(child) for node in root.iter() if node.get('__pdf_craft_missing') == '1'
                    for child in node.iter()}

        async def notify_ready(root, position):
            while notifications and notifications[0][0] <= position:
                _, origin, texts, block = notifications.popleft()
                if id(block) not in rejected(root):
                    await invoke_callback(on_mapping, (origin, texts))

        def accepted(root, mappings):
            missing = rejected(root)
            return [(block, texts) for block, texts in mappings if id(block) not in missing]


        def units(group):
            if isinstance(group, _EmptyElement):
                yield group
                return
            head, body, tail = self._truncate_and_transform_group(group)
            head = [segment.clone() for segment in head]
            tail = [segment.clone() for segment in tail]
            for segment in head + tail:
                setattr(segment, "_translation_context", True)
            body_ids = {id(segment) for segment in body}
            segments = head + body + tail
            # One bounded splitter group owns one free translation and XML fill.
            yield _Work(segments, [id(segment) in body_ids for segment in segments])

        def batch_works(groups):
            for group in groups:
                yield list(units(group))

        async def execute(work):
            if isinstance(work, _EmptyElement):
                await invoke_callback(on_empty, work.root)
                return (), 0
            async with diagnostics.group(work.segments) if diagnostics is not None else nullcontext(0) as key:
                mappings = await map(work.segments)
                return [(origin, target) for origin, target, primary in
                        zip(work.segments, mappings, work.primary, strict=True) if primary], key

        # A bounded group is accepted atomically. Other groups can advance
        # independently within the operation window.
        capacity = window
        if isinstance(elements, AsyncIterable):
            async def work_items():
                async with aclosing(self._async_groups(
                    elements, callbacks, on_mapping is not None, progress,
                    on_empty is not None, ownership, expanded,
                )) as groups:
                    async for group_batch in groups:
                        for works in batch_works(group_batch):
                            yield works
            results = run_async_parameters(work_items(), execute, capacity, preserve_sources=False, expand=lambda works: works, ordered=ordered_roots)
        else:
            work_items_sync = (works for group in self._split_into_serial_groups(
                elements, callbacks, on_mapping is not None, progress, on_empty is not None, ownership, expanded,
            ) for works in batch_works([group]))
            results = run_async_parameters(work_items_sync, execute, capacity, expand=lambda works: works)
        async with aclosing(results):
            async for mapping_pairs, key in results:
                async with diagnostics.processing(key) if diagnostics is not None else nullcontext():
                    for origin, target in mapping_pairs:
                        origin_element = origin.head.root
                        if not ordered_roots:
                            key = source_identity(origin_element)
                            current_element, mapping_buffer = root_states.setdefault(key, (origin_element, []))
                        if current_element is None:
                            current_element = origin_element

                        if ordered_roots and source_identity(current_element) != source_identity(origin_element):
                            assert current_element is not None
                            yield current_element, accepted(current_element, mapping_buffer)
                            current_element = origin_element
                            mapping_buffer = []

                        owner = ownership.unit(origin) if ownership is not None else origin.parent
                        if isinstance(elements, AsyncIterable):
                            owner = rebind(current_element, origin, owner)
                        assert owner is not None
                        owner.set('__pdf_craft_unit', ownership.key(owner) if ownership is not None else
                                  owner.get(NODE_KEY) or str(id(owner)))
                        if target is None:
                            owner.set('__pdf_craft_missing', '1')
                        if target:
                            assert current_element is not None
                            block_element, text_segments = target
                            if isinstance(elements, AsyncIterable):
                                block_element = rebind(current_element, origin, block_element)
                            assert block_element is not None
                            block_element = callbacks.interrupt_block_element(block_element)
                            text_segments = list(callbacks.interrupt_translated_text_segments(text_segments))
                            if isinstance(elements, AsyncIterable):
                                # Adapters may restore a cached source pointer or
                                # materialize fresh translated nodes. Bind the source
                                # AFTER the callback, never return a detached root.
                                block_element = rebind(current_element, origin, block_element)
                                by_key = {node.get(NODE_KEY): node for node in current_element.iter() if node.get(NODE_KEY) is not None}
                                for segment in text_segments:
                                    segment.parent_stack = [by_key.get(parent.get(NODE_KEY), parent) for parent in segment.parent_stack]
                            if text_segments:
                                if on_mapping is not None:
                                    notifications.append((origin._notification_end, origin, text_segments, block_element))
                                mapping_buffer.append((block_element, text_segments))
                        if on_mapping is not None:
                            await notify_ready(current_element, origin._notification_index)
                        if not ordered_roots:
                            key = source_identity(origin_element)
                            completed[key] = completed.get(key, 0) + 1
                            if completed[key] == expected.get(key):
                                completed.pop(key)
                                expected.pop(key)
                                root_states.pop(key)
                                assert current_element is not None
                                yield current_element, accepted(current_element, mapping_buffer)
                                current_element = None
                                mapping_buffer = []

        if ordered_roots and current_element is not None:
            yield current_element, accepted(current_element, mapping_buffer)

    async def _async_groups(self, elements, callbacks, track_origins, progress, include_empty, ownership, on_expanded):
        """Feed the same budget splitter across roots with bounded lookahead.

        A deadline bounds first delivery when input pauses; it is a source
        collection bound, not a race between independent model calls. Keep an
        outstanding read alive across a short tail flush and close it on exit.
        """
        iterator = aiter(elements)
        incoming = None
        exhausted = False
        try:
            while not exhausted:
                roots = deque()
                deadline = None
                score = 0
                while len(roots) < MAX_BATCH_UNITS:
                    if incoming is None:
                        incoming = asyncio.ensure_future(anext(iterator))
                    if deadline is not None:
                        done, _ = await asyncio.wait([incoming], timeout=min(_SOURCE_IDLE_TIMEOUT, max(0, deadline - time.monotonic())))
                        if not done:
                            break
                    try:
                        root = await incoming
                    except StopAsyncIteration:
                        exhausted = True
                        incoming = None
                        break
                    incoming = None
                    roots.append(root)
                    if deadline is None:
                        deadline = time.monotonic() + _SOURCE_READY_TIMEOUT
                    # Bound DOM lookahead by the existing score as well as
                    # root count. Projection/tokenization happens once below.
                    score += len(self._encoding.encode(''.join(root.itertext()), disallowed_special=()))
                    if score >= self._max_group_score:
                        break
                def consume_roots():
                    while roots:
                        yield roots.popleft()
                yield self._split_into_serial_groups(
                    consume_roots(), callbacks, track_origins, progress, include_empty, ownership, on_expanded,
                )
        finally:
            if incoming is not None:
                incoming.cancel()
                from pdf_craft.concurrency import settle_tasks
                await settle_tasks([incoming])
            close = getattr(iterator, 'aclose', None)
            if close is not None:
                await close()

    def _split_into_serial_groups(self, elements: Iterable[Element], callbacks: Callbacks,
                                  track_origins: bool = False, progress: OperationProgress | None = None,
                                  include_empty: bool = False, ownership=None, on_expanded=None):
        def generate():
            for element in elements:
                resources = self._expand_to_resources(element, callbacks, track_origins, progress, ownership, on_expanded)
                first = next(resources, None)
                if first is None:
                    if include_empty:
                        yield _EmptyElement(element)
                    continue
                while first is not None:
                    # resource_segmentation collects its input before grouping.
                    # Bound that collection even for one enormous XHTML root;
                    # the existing merger below joins the chunk's short tail
                    # with its successor using the unchanged score budget.
                    part = list(islice(chain((first,), resources), MAX_BATCH_UNITS))
                    yield from split(
                        max_segment_count=self._max_group_score,
                        border_incision=_PAGE_INCISION,
                        resources=iter(part),
                    )
                    first = next(resources, None)

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
            if (next_sum_count + next_group.tail_remain_count > self._max_group_score
                    or len(list(self._expand_inline_segments(group.body))) +
                    len(list(self._expand_inline_segments(next_group.body))) > MAX_BATCH_UNITS):
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

    def _expand_to_resources(self, element: Element, callbacks: Callbacks, track_origins: bool = False, progress: OperationProgress | None = None, ownership=None, on_expanded=None):
        def expand(element: Element):
            # Weights belong to original blocks, before formula/context transport
            # projection. Never count temporary tokens or prompt context.
            from ...word_count import xml_block_word_counts
            def count_blocks():
                counts = {}
                for block, words in xml_block_word_counts(element):
                    counts[id(block)] = counts.get(id(block), 0) + words
                return counts
            original_blocks = progress.attempt_sync(count_blocks) or {} if progress is not None else {}
            def source_segments():
                for text in search_text_segments(element, track_origins=track_origins):
                    if progress is not None and not progress.disabled:
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

        if track_origins:
            # A platform owner can occur on both sides of nested blocks. Wait
            # only for its own last piece (and any enclosing atomic owner),
            # never for an unrelated sibling or a subsequent XML root.
            inlines = list(expand(element))
            last = {id(ownership.unit(inline) if ownership is not None else inline.parent): index
                    for index, inline in enumerate(inlines)}
            for index, inline in enumerate(inlines):
                inline._notification_index = index  # pylint: disable=protected-access
                inline._notification_end = max(  # pylint: disable=protected-access
                    (last[id(parent)] for parent in inline.head.parent_stack if id(parent) in last),
                    default=index)
            inline_segment_generator = iter(inlines)
        else:
            inline_segment_generator = expand(element)
        start_incision = _PAGE_INCISION
        count = 1
        inline_segment = next(inline_segment_generator, None)
        if inline_segment is None:
            return

        while True:
            next_inline_segment = next(inline_segment_generator, None)
            if next_inline_segment is None:
                break
            count += 1

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
        if on_expanded is not None:
            on_expanded(element, count)

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
