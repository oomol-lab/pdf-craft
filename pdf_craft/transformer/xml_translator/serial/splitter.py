from collections.abc import Callable, Generator, Iterable

from .chunk import split_into_chunks
from .segment import ST, T


def split(
    segments: Iterable[ST],
    transform: Callable[[list[ST]], list[T]],
    max_group_tokens: int,
) -> Generator[T, None, None]:
    for group in split_into_chunks(segments, max_group_tokens):
        head = list(
            _truncate_extra_content(
                segments=group.head,
                remain_left=False,
                remain_tokens=group.head_remain_tokens,
            )
        )
        tail = list(
            _truncate_extra_content(
                segments=group.tail,
                remain_left=True,
                remain_tokens=group.tail_remain_tokens,
            )
        )
        transformed = transform(head + group.body + tail)

        if len(tail) > 0:  # 避免 target[N:-0] 切片错误
            yield from transformed[len(head) : -len(tail)]
        else:
            yield from transformed[len(head) :]


def _truncate_extra_content(segments: list[ST], remain_left: bool, remain_tokens: int):
    tokens_list: list[int] = [segment.tokens for segment in segments]
    segments = list(segments)
    for tokens in tokens_list if remain_left else reversed(tokens_list):
        if remain_tokens <= 0:
            break
        next_segment = segments.pop(0) if remain_left else segments.pop()
        if remain_tokens < tokens:
            if remain_left:
                next_segment = next_segment.truncate_after_head(remain_tokens)
            else:
                next_segment = next_segment.truncate_before_tail(remain_tokens)
            remain_tokens = 0
        else:
            remain_tokens -= tokens
        yield next_segment
