from collections.abc import Generator
from copy import deepcopy
from xml.etree.ElementTree import Element, indent

from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import (
    BlockContentError, BlockUnexpectedIDError, BlockSegment, TextSegment,
    search_text_segments, TextPosition,
)
from pdf_craft.transformer.xml_translator.xml import encode_friendly
from pdf_craft.transformer.xml_translator.xml.matching import element_id
from pdf_craft.common.whitespace import is_whitespace
from .stream_mapper import InlineSegmentMapping
from .validation import generate_error_message, nest_as_errors_group, truncate_errors_group


class HillClimbing:
    """Read-only source validation and whole-candidate mapping.

    Repair draft selection belongs to the XML fill loop. Never merge blocks
    from different answers here.
    """
    def __init__(
        self,
        encoding: Encoding,
        max_fill_displaying_errors: int,
        block_segment: BlockSegment,
    ) -> None:
        self._encoding: Encoding = encoding
        self._max_fill_displaying_errors: int = max_fill_displaying_errors
        self._accepted: Element | None = None
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
                indent(block)
        return template

    def gen_mappings(self) -> Generator[InlineSegmentMapping | None, None, None]:
        if self._accepted is None:
            yield from (None for _ in self._block_segment)
        else:
            yield from self.candidate_mappings(self._accepted)

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
        if any(not isinstance(error, (BlockContentError, BlockUnexpectedIDError)) for error in self._block_segment.validate(element)):
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
        error = self.validate(element)
        if error is not None:
            return error
        self._accepted = deepcopy(element)
        return None

    def inline_feedback_blocks(self, element: Element) -> list[dict[str, str]]:
        """Show both allocations for missing inline identities, without guessing semantics."""
        sources = {node.get('id'): node for node in self.request_template()}
        candidates = {node.get('id'): node for node in element}
        blocks = []
        for error in self._block_segment.validate(element):
            if isinstance(error, BlockContentError):
                key = str(error.id)
                if key in sources and key in candidates:
                    source, candidate = encode_friendly(sources[key]), encode_friendly(candidates[key])
                    # The complete template and previous answer are already
                    # in the request. Repeating huge trees hides the actual
                    # missing-node diagnostic instead of clarifying it.
                    if len(source) + len(candidate) <= 4096:
                        blocks.append({'source': source, 'candidate': candidate})
        return blocks[:self._max_fill_displaying_errors]

    def validate(self, element: Element) -> str | None:
        """Validate the raw answer without adopting or rewriting any of it."""
        errors_group = nest_as_errors_group(
            errors=self._block_segment.validate(element),
        )
        if errors_group is None:
            return None

        origin_errors_count = errors_group.errors_count
        errors_group = truncate_errors_group(
            errors_group=errors_group,
            max_errors=max(1, self._max_fill_displaying_errors),
        )
        if errors_group is None:
            return None

        message = generate_error_message(
            encoding=self._encoding,
            errors_group=errors_group,
            omitted_count=origin_errors_count - errors_group.errors_count,
            required_tree=self.request_template(),
        )
        return message


def _submitted_segments(element: Element) -> list[TextSegment]:
    """An accepted empty allocation must clear its source, not restore it."""
    return list(search_text_segments(element, normalize_whitespace=False)) or [
        TextSegment('', [element], 0, 0, 1, TextPosition.TEXT)]
