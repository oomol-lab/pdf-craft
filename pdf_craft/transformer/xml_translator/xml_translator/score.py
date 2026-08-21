from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum, auto

from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import InlineSegment, TextSegment
from .common import DATA_ORIGIN_LEN_KEY

_ID_WEIGHT = 80
_ELLIPSIS = "..."


@dataclass
class ScoreSegment:
    text_segment: TextSegment
    left_parents: list[InlineSegment]
    right_parents: list[InlineSegment]
    text_tokens: list[int]
    score: int


def expand_to_score_segments(encoding: Encoding, inline_segment: InlineSegment) -> Generator[ScoreSegment, None, None]:
    for i, score_segment in enumerate(_do_expand_inline_segment(inline_segment)):
        xml_text = "".join(
            _render_score_segment(
                score_segment=score_segment,
                is_first=(i == 0),
            )
        )
        score_segment.text_tokens = encoding.encode(score_segment.text_segment.text)
        score_segment.score = len(encoding.encode(xml_text)) + sum(
            _ID_WEIGHT for parent in score_segment.left_parents if parent.id is not None
        )
        yield score_segment


def truncate_score_segment(
    encoding: Encoding,
    score_segment: ScoreSegment,
    remain_head: bool,
    remain_score: int,
):
    fixed_score = score_segment.score - len(score_segment.text_tokens)
    if remain_score <= fixed_score:
        # 裁剪仅能减少 text 部分的 tokens 数。
        # 而 XML 本身头尾占用的 tokens 数，以及 ID 占用加权分属于 fixed_score 部分，无法裁剪
        # 当发现将文字删光后才能达标时，不如直接放弃整段内容
        return None

    remain_text_tokens_count = remain_score - fixed_score

    # remain_text_tokens_count cannot be 0 here
    if remain_head:
        remain_text = encoding.decode(score_segment.text_tokens[:remain_text_tokens_count])
    else:
        remain_text = encoding.decode(score_segment.text_tokens[-remain_text_tokens_count:])

    if not remain_text.strip():
        return None

    if remain_head:
        remain_text = f"{remain_text} {_ELLIPSIS}"
    else:
        remain_text = f"{_ELLIPSIS} {remain_text}"

    text_segment = score_segment.text_segment.clone()
    text_segment.text = remain_text

    return ScoreSegment(
        text_segment=text_segment,
        left_parents=score_segment.left_parents,
        right_parents=score_segment.right_parents,
        text_tokens=encoding.encode(remain_text),
        score=remain_text_tokens_count + fixed_score,
    )


def _render_score_segment(score_segment: ScoreSegment, is_first: bool):
    for i, parent in enumerate(score_segment.left_parents):
        yield "<"
        yield parent.parent.tag
        if parent.id is not None:
            yield ' id="99"'
        if is_first and i == 0:
            yield " "
            yield DATA_ORIGIN_LEN_KEY
            yield '="9999"'
        yield ">"

    yield score_segment.text_segment.text

    for parent in reversed(score_segment.right_parents):
        yield "</"
        yield parent.parent.tag
        yield ">"


def _do_expand_inline_segment(inline_segment: InlineSegment):
    text_segment: TextSegment | None = None
    left_parents: list[InlineSegment] = []
    right_parents: list[InlineSegment] = []

    for item in _expand_as_wrapped(inline_segment):
        if isinstance(item, TextSegment):
            if text_segment is None:
                text_segment = item
            else:
                yield ScoreSegment(
                    text_segment=text_segment,
                    left_parents=left_parents,
                    right_parents=right_parents,
                    text_tokens=[],
                    score=0,
                )
                text_segment = item
                left_parents = []
                right_parents = []

        elif isinstance(item, tuple):
            child_inline_segment, orientation = item
            if orientation == _Orientation.UP:
                if text_segment is not None:
                    yield ScoreSegment(
                        text_segment=text_segment,
                        left_parents=left_parents,
                        right_parents=right_parents,
                        text_tokens=[],
                        score=0,
                    )
                    text_segment = None
                    left_parents = []
                    right_parents = []
                left_parents.append(child_inline_segment)

            elif orientation == _Orientation.DOWN:
                if text_segment is None:
                    left_parents.clear()
                else:
                    right_parents.append(child_inline_segment)

    if text_segment is not None:
        yield ScoreSegment(
            text_segment=text_segment,
            left_parents=left_parents,
            right_parents=right_parents,
            text_tokens=[],
            score=0,
        )


class _Orientation(Enum):
    DOWN = auto()
    UP = auto()


def _expand_as_wrapped(inline_segment: InlineSegment):
    yield (inline_segment, _Orientation.UP)
    for child in inline_segment.children:
        if isinstance(child, InlineSegment):
            yield from _expand_as_wrapped(child)
        elif isinstance(child, TextSegment):
            yield child
    yield (inline_segment, _Orientation.DOWN)
