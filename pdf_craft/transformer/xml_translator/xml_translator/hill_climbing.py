from collections.abc import Generator
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import BlockSegment, BlockSubmitter, TextSegment, search_text_segments
from pdf_craft.transformer.xml_translator.xml import plain_text
from .common import DATA_ORIGIN_LEN_KEY
from .stream_mapper import InlineSegmentMapping
from .validation import LEVEL_DEPTH, generate_error_message, nest_as_errors_group, truncate_errors_group


@dataclass
class _BlockStatus:
    weight: int
    submitter: BlockSubmitter


# 以爬山算法，将 LLM 中提交的内容中挑选出完成度更高的部分。
# 它通过拒绝每个子部分的相对低完成度提交，锁定每个子部分只能往更高完成度的方向移动
class HillClimbing:
    def __init__(
        self,
        encoding: Encoding,
        max_fill_displaying_errors: int,
        block_segment: BlockSegment,
    ) -> None:
        self._encoding: Encoding = encoding
        self._max_fill_displaying_errors: int = max_fill_displaying_errors
        self._block_statuses: dict[int, _BlockStatus] = {}
        self._block_segment: BlockSegment = block_segment

    def request_element(self) -> Element:
        element = self._block_segment.create_element()
        for child_element in element:
            text = plain_text(child_element)
            tokens = self._encoding.encode(text)
            child_element.set(DATA_ORIGIN_LEN_KEY, str(len(tokens)))
        return element

    def gen_mappings(self) -> Generator[InlineSegmentMapping | None, None, None]:
        for inline_segment in self._block_segment:
            id = inline_segment.id
            assert id is not None
            status = self._block_statuses.get(id, None)
            text_segments: list[TextSegment] | None = None
            if status is None:
                yield None
            else:
                submitted_element = status.submitter.submitted_element
                text_segments = list(search_text_segments(submitted_element))
                yield inline_segment.parent, text_segments

    def submit(self, element: Element) -> str | None:
        error_message, block_weights = self._validate_block_weights_and_error_message(element)

        for submitter in self._block_segment.submit(element):
            weight: int = 0  # 未出现在 block_weights 说明没有错误，已完成
            if block_weights:
                weight = block_weights.get(submitter.id, 0)
            status = self._block_statuses.get(submitter.id, None)
            if status is None:
                self._block_statuses[submitter.id] = _BlockStatus(
                    weight=weight,
                    submitter=submitter,
                )
            elif weight < status.weight:
                status.weight = weight
                status.submitter = submitter

        return error_message

    def _validate_block_weights_and_error_message(self, element: Element) -> tuple[str | None, dict[int, int] | None]:
        errors_group = nest_as_errors_group(
            errors=self._block_segment.validate(element),
        )
        if errors_group is None:
            return None, None

        block_weights: dict[int, int] = {}
        for block_group in errors_group.block_groups:
            block_id = block_group.block_id
            status = self._block_statuses.get(block_id, None)
            block_weights[block_id] = block_group.weight
            if status is not None and status.weight > block_group.weight:
                # 本轮完成度得到改善（weight 下降）应该排后，让出注意力给完成度尚未改善的部分
                for child_error in block_group.errors:
                    child_error.level -= LEVEL_DEPTH

        origin_errors_count = errors_group.errors_count
        errors_group = truncate_errors_group(
            errors_group=errors_group,
            max_errors=self._max_fill_displaying_errors,
        )
        if errors_group is None:
            return None, block_weights

        message = generate_error_message(
            encoding=self._encoding,
            errors_group=errors_group,
            omitted_count=origin_errors_count - errors_group.errors_count,
        )
        return message, block_weights
