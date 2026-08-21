from collections.abc import Generator, Iterable
from dataclasses import dataclass
from typing import Generic

from resource_segmentation import Resource, Segment, split

from .segment import ST

_INCISION = 0


@dataclass
class Chunk(Generic[ST]):
    head_remain_tokens: int
    tail_remain_tokens: int
    head: list[ST]
    body: list[ST]
    tail: list[ST]


def split_into_chunks(segments: Iterable[ST], max_group_tokens: int) -> Generator[Chunk[ST], None, None]:
    for group in split(
        max_segment_count=max_group_tokens,
        gap_rate=0.07,
        tail_rate=0.5,
        border_incision=_INCISION,
        resources=(
            Resource(
                count=segment.tokens,
                start_incision=_INCISION,
                end_incision=_INCISION,
                payload=segment,
            )
            for segment in segments
        ),
    ):
        yield Chunk(
            head_remain_tokens=group.head_remain_count,
            tail_remain_tokens=group.tail_remain_count,
            head=list(_expand_payloads(group.head)),
            body=list(_expand_payloads(group.body)),
            tail=list(_expand_payloads(group.tail)),
        )


def _expand_payloads(target: list[Resource[ST] | Segment[ST]]) -> Generator[ST, None, None]:
    for item in target:
        if isinstance(item, Resource):
            yield item.payload
        elif isinstance(item, Segment):
            for resource in item.resources:
                yield resource.payload
