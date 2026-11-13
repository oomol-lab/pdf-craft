from collections.abc import Generator
from dataclasses import dataclass
from typing import TypeAlias, cast
from xml.etree.ElementTree import Element
from pdf_craft.common.whitespace import is_whitespace

from .common import FoundInvalidIDError, validate_id_in_element
from .inline_segment import InlineError, InlineSegment
from .sibling_inline_scope import SCOPE_ATTRIBUTE, SiblingInlineScopes
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
    expected: list["ImmutableFingerprint"]
    found: list["ImmutableFingerprint"]


class SourceAnchorError(ValueError):
    """The program supplied inconsistent anchors; model repair cannot help."""


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
        self._inline_scopes = SiblingInlineScopes(
            inline_segments, {item.before_block_index for item in immutable_elements or []})
        id_generator = IDGenerator()
        for inline_segment in inline_segments:
            inline_segment.id = id_generator.next_id()
            inline_segment.recreate_ids(id_generator)

        self._inline_scopes.bind_ids()
        self._root_tag: str = root_tag
        self._inline_segments: list[InlineSegment] = inline_segments
        self._id2inline_segment: dict[int, InlineSegment] = dict((cast(int, s.id), s) for s in self._inline_segments)
        self._immutable_elements = immutable_elements or []
        self._validate_source_anchors()

    def _validate_source_anchors(self) -> None:
        """Reject impossible source contracts before any model request."""
        if not self._immutable_elements:
            return
        keys: set[str] = set()
        previous = 0
        for item in self._immutable_elements:
            anchor = item.element
            key = anchor.get('anchor_key')
            if (anchor.tag != 'anchor' or not key or key in keys
                    or anchor.get('id') is not None or len(anchor) or (anchor.text or '').strip()):
                raise SourceAnchorError(f'Invalid or duplicate source anchor identity: {key!r}')
            keys.add(key)
            if not previous <= item.before_block_index <= len(self._inline_segments):
                raise SourceAnchorError(f'Invalid source anchor boundary: {key!r} at {item.before_block_index}')
            previous = item.before_block_index
        if not self._immutable_positions_are_valid(self.create_element()):
            raise SourceAnchorError('Source anchor template cannot satisfy its own boundary contract')

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
            node = inline_segment.create_element()
            scope = self._inline_scopes.label(inline_segment.id)
            if scope is not None:
                node.set(SCOPE_ATTRIBUTE, scope)
            root_element.append(node)
        for immutable in immutable_by_index.get(len(self._inline_segments), []):
            root_element.append(_clone_immutable_element(immutable.element))
        return root_element

    def validate(self, validated_element: Element) -> Generator[BlockError | FoundInvalidIDError, None, None]:
        # IDs share one request-local namespace, including nested inline nodes.
        # Validation and adoption must never choose different duplicate nodes.
        seen: set[int] = set()
        for node in validated_element.iter():
            if node is validated_element or node.get("id") is None:
                continue
            identifier = validate_id_in_element(node)
            if isinstance(identifier, int):
                if identifier in seen:
                    yield FoundInvalidIDError(node.get("id"), node,
                        f"Duplicate ID {identifier}: keep exactly one node for each required ID.")
                seen.add(identifier)
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

        candidates = {identity: node for node in validated_element
                      if (identity := id_in_element(node)) is not None}
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
                    inline_errors = list(self._inline_scopes.validate(
                        inline_segment, child_validated_element, candidates))

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
        found: dict[ImmutableFingerprint, list[int]] = {}
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
            # Delivery only: a validated plain fragment tail has one output
            # owner. Preserve its characters when binding source attributes;
            # acceptance itself never edits the model's tree.
            if child_element.tag == 'fragment' and not len(child_element) and any(not is_whitespace(char) for char in child_element.tail or ''):
                from copy import deepcopy
                child_element = deepcopy(child_element)
                child_element.text = (child_element.text or '') + (child_element.tail or '')
                child_element.tail = None
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
                submitted_element=self._inline_scopes.adopt(inline_segment, child_element),
            )


def _clone_immutable_element(element: Element) -> Element:
    """Clone only a structural node; opaque nodes must never carry text."""
    return Element(element.tag, element.attrib)


ImmutableFingerprint: TypeAlias = tuple[
    str, tuple[tuple[str, str], ...], str, str, tuple["ImmutableFingerprint", ...],
]


def _immutable_fingerprint(element: Element) -> ImmutableFingerprint:
    """Return an exact self-closing identity for an immutable template node.

    Formatting whitespace in ``text``/``tail`` is ignored because a pretty
    printed XML response necessarily gives a self-closing child a whitespace
    tail.  Any visible text, tail text, or nested element is a protocol error.
    """
    attributes = tuple(sorted(element.attrib.items()))
    return (
        element.tag,
        attributes,
        (element.text or "").strip(),
        (element.tail or "").strip(),
        tuple(_immutable_fingerprint(child) for child in element),
    )
