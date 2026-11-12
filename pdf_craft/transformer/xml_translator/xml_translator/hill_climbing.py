from collections.abc import Generator
from copy import deepcopy
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import (
    BlockContentError, BlockImmutableElementsError, BlockSegment, BlockSubmitter, FoundInvalidIDError, TextSegment,
    search_text_segments, TextPosition,
)
from pdf_craft.transformer.xml_translator.xml import encode_friendly
from pdf_craft.transformer.xml_translator.xml.matching import element_id
from pdf_craft.common.whitespace import is_whitespace
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
        # Source text length is neither output capacity nor a model obligation.
        return self._block_segment.create_element()

    def request_template(self) -> Element:
        """Keep identities/resources without suggesting source words to restore."""
        template = deepcopy(self.request_element())
        for block in template:
            if block.get('id') is not None:
                for node in block.iter():
                    node.text = node.tail = None
        return template

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
                text_segments = _submitted_segments(submitted_element)
                yield inline_segment.parent, text_segments

    def candidate_mappings(self, element: Element) -> list[InlineSegmentMapping | None]:
        """Materialize a private, validated candidate before committing it."""
        candidates = {item.id: item for item in self._block_segment.submit(element)}
        return [(segment.parent, _submitted_segments(candidates[segment.id].submitted_element))
                if segment.id in candidates else None for segment in self._block_segment]

    def candidate_blocks_for_diagnostics(self, element: Element) -> list[Element] | None:
        """Read raw text despite inline errors, only with intact outer ownership.

        This is a diagnostic view, never a mapping or an accepted candidate.
        Missing/duplicate blocks and broken resource boundaries cannot supply
        trustworthy delivery-order text.
        """
        if any(not isinstance(error, BlockContentError) for error in self._block_segment.validate(element)):
            return None
        if any(not is_whitespace(char) for char in element.text or ''):
            return None
        candidates = {element_id(node): node for node in element}
        expected = {segment.id for segment in self._block_segment}
        if any(any(not is_whitespace(char) for char in ''.join(node.itertext()) + (node.tail or ''))
               for identity, node in candidates.items() if identity not in expected):
            return None
        return [candidates[segment.id] for segment in self._block_segment]

    def submit(self, element: Element) -> str | None:
        error_message, block_weights = self._validate_block_weights_and_error_message(element)

        # Opaque structural tokens are not a partially useful translation.
        # Do not let an invalid response become the hill-climbing baseline:
        # its block text may otherwise survive a later repaired response with
        # an equal score.
        if error_message is not None and any(
            isinstance(error, (BlockImmutableElementsError, FoundInvalidIDError))
            for error in self._block_segment.validate(element)
        ):
            return error_message

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

    def inline_feedback_blocks(self, element: Element) -> list[dict[str, str]]:
        """Show both allocations for missing inline identities, without guessing semantics."""
        sources = {node.get('id'): node for node in self.request_template()}
        candidates = {node.get('id'): node for node in element}
        blocks = []
        for error in self._block_segment.validate(element):
            if isinstance(error, BlockContentError):
                key = str(error.id)
                if key in sources and key in candidates:
                    blocks.append({'source': encode_friendly(sources[key]),
                                   'candidate': encode_friendly(candidates[key])})
        return blocks[:self._max_fill_displaying_errors]

    def validate(self, element: Element) -> str | None:
        """Validate a candidate without admitting it to the hill-climbing state.

        Some format-specific callers need to reject a structurally valid
        response before it becomes the baseline for a block.  In particular,
        PCEX's zero-width asset anchors must not let the fill model introduce
        visible characters at a fragment boundary.  A rejected candidate must
        not be retained: equally valid repaired responses otherwise cannot
        replace it in the monotonic completion tracker.
        """
        error_message, _ = self._validate_block_weights_and_error_message(element)
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


def _submitted_segments(element: Element) -> list[TextSegment]:
    """An accepted empty allocation must clear its source, not restore it."""
    return list(search_text_segments(element, normalize_whitespace=False)) or [
        TextSegment('', [element], 0, 0, 1, TextPosition.TEXT)]
