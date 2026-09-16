from collections.abc import Generator
from dataclasses import dataclass
from typing import cast
from xml.etree.ElementTree import Element

from .common import FoundInvalidIDError, validate_id_in_element
from .inline_segment import InlineError, InlineSegment
from .text_segment import TextSegment
from .utils import IDGenerator, id_in_element


@dataclass
class BlockSubmitter:
    id: int
    origin_text_segments: list[TextSegment]
    submitted_element: Element


@dataclass
class BlockWrongTagError:
    block: tuple[int, Element] | None  # (block_id, block_element) | None 表示根元素
    expected_tag: str
    instead_tag: str


@dataclass
class BlockUnexpectedIDError:
    id: int
    element: Element


@dataclass
class BlockExpectedIDsError:
    id2element: dict[int, Element]


@dataclass
class BlockContentError:
    id: int
    element: Element
    errors: list[InlineError | FoundInvalidIDError]


@dataclass(frozen=True)
class ImmutableBlockElement:
    """A non-text node that must survive a fill-template round trip.

    ``before_block_index`` describes its position in the generated ``<xml>``
    template.  It is intentionally an index rather than an XML path: the
    stream mapper may split a chapter into independent translation groups.
    """
    element: Element
    before_block_index: int


@dataclass
class BlockImmutableElementsError:
    """The fill response changed an opaque structural token sequence."""
    expected: list[tuple[str, tuple[tuple[str, str], ...]]]
    found: list[tuple[str, tuple[tuple[str, str], ...]]]


BlockError = (
    BlockWrongTagError | BlockUnexpectedIDError | BlockExpectedIDsError |
    BlockContentError | BlockImmutableElementsError
)


class BlockSegment:
    def __init__(
        self,
        root_tag: str,
        inline_segments: list[InlineSegment],
        immutable_elements: list[ImmutableBlockElement] | None = None,
    ) -> None:
        id_generator = IDGenerator()
        for inline_segment in inline_segments:
            inline_segment.id = id_generator.next_id()
            inline_segment.recreate_ids(id_generator)

        self._root_tag: str = root_tag
        self._inline_segments: list[InlineSegment] = inline_segments
        self._id2inline_segment: dict[int, InlineSegment] = dict((cast(int, s.id), s) for s in self._inline_segments)
        self._immutable_elements = immutable_elements or []

    def __iter__(self) -> Generator[InlineSegment, None, None]:
        yield from self._inline_segments

    def create_element(self) -> Element:
        root_element = Element(self._root_tag)
        immutable_by_index: dict[int, list[ImmutableBlockElement]] = {}
        for immutable in self._immutable_elements:
            immutable_by_index.setdefault(immutable.before_block_index, []).append(immutable)
        for index, inline_segment in enumerate(self._inline_segments):
            for immutable in immutable_by_index.get(index, []):
                root_element.append(_clone_immutable_element(immutable.element))
            root_element.append(inline_segment.create_element())
        for immutable in immutable_by_index.get(len(self._inline_segments), []):
            root_element.append(_clone_immutable_element(immutable.element))
        return root_element

    def validate(self, validated_element: Element) -> Generator[BlockError | FoundInvalidIDError, None, None]:
        if validated_element.tag != self._root_tag:
            yield BlockWrongTagError(
                block=None,
                expected_tag=self._root_tag,
                instead_tag=validated_element.tag,
            )

        expected_immutable = [_immutable_fingerprint(item.element) for item in self._immutable_elements]
        found_immutable = [
            _immutable_fingerprint(child)
            for child in validated_element
            if child.tag == "anchor" or child.get("anchor_key") is not None
        ]
        if (
            found_immutable != expected_immutable
            or not self._immutable_positions_are_valid(validated_element)
        ):
            yield BlockImmutableElementsError(expected_immutable, found_immutable)

        remain_expected_elements: dict[int, Element] = dict(
            (id, inline_segment.parent) for id, inline_segment in self._id2inline_segment.items()
        )
        for child_validated_element in validated_element:
            if child_validated_element.tag == "anchor" or child_validated_element.get("anchor_key") is not None:
                continue
            element_id = validate_id_in_element(child_validated_element)
            if isinstance(element_id, FoundInvalidIDError):
                yield element_id
            else:
                inline_segment = self._id2inline_segment.get(element_id, None)
                if inline_segment is None:
                    yield BlockUnexpectedIDError(
                        id=element_id,
                        element=child_validated_element,
                    )
                else:
                    if inline_segment.parent.tag != child_validated_element.tag:
                        yield BlockWrongTagError(
                            block=(cast(int, inline_segment.id), inline_segment.parent),
                            expected_tag=inline_segment.parent.tag,
                            instead_tag=child_validated_element.tag,
                        )

                    remain_expected_elements.pop(element_id, None)
                    inline_errors = list(inline_segment.validate(child_validated_element))

                    if inline_errors:
                        yield BlockContentError(
                            id=element_id,
                            element=child_validated_element,
                            errors=inline_errors,
                        )

        if remain_expected_elements:
            yield BlockExpectedIDsError(id2element=remain_expected_elements)

    def _immutable_positions_are_valid(self, validated_element: Element) -> bool:
        """Ensure tokens remain between the same logical translation blocks."""
        if not self._immutable_elements:
            return True
        child_positions = {id(child): index for index, child in enumerate(validated_element)}
        block_positions: list[int] = []
        for inline_segment in self._inline_segments:
            block_id = inline_segment.id
            if block_id is None:
                return False
            position = next(
                (
                    index for index, child in enumerate(validated_element)
                    if id_in_element(child) == block_id
                ),
                None,
            )
            if position is None:
                return False
            block_positions.append(position)

        expected = [_immutable_fingerprint(item.element) for item in self._immutable_elements]
        found: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = {}
        for child in validated_element:
            fingerprint = _immutable_fingerprint(child)
            if fingerprint in expected:
                found.setdefault(fingerprint, []).append(child_positions[id(child)])

        for immutable, fingerprint in zip(self._immutable_elements, expected, strict=True):
            positions = found.get(fingerprint, [])
            if len(positions) != 1:
                return False
            position = positions[0]
            before = immutable.before_block_index
            if any(block_position >= position for block_position in block_positions[:before]):
                return False
            if any(block_position <= position for block_position in block_positions[before:]):
                return False
        return True
    def submit(self, target: Element) -> Generator[BlockSubmitter, None, None]:
        for child_element in target:
            element_id = id_in_element(child_element)
            if element_id is None:
                continue
            inline_segment = self._id2inline_segment.get(element_id, None)
            if inline_segment is None:
                continue
            inline_segment_id = inline_segment.id
            assert inline_segment_id is not None
            yield BlockSubmitter(
                id=inline_segment_id,
                origin_text_segments=list(inline_segment),
                submitted_element=inline_segment.assign_attributes(child_element),
            )


def _clone_immutable_element(element: Element) -> Element:
    """Clone only a structural node; opaque nodes must never carry text."""
    return Element(element.tag, element.attrib)


def _immutable_fingerprint(element: Element) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Return an exact, text-free identity for an immutable template node."""
    attributes = tuple(sorted(element.attrib.items()))
    return element.tag, attributes
