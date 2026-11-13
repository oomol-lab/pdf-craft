"""Request-local relocation of inline subtrees between real sibling blocks.

The transport root can aggregate unrelated chapters. Only source-tree siblings
inside one protected-resource interval share a scope; it never defines one.
"""
from collections import Counter
from collections.abc import Generator
from xml.etree.ElementTree import Element

from ..xml.matching import element_id
from .common import FoundInvalidIDError, validate_id_in_element
from .inline_segment import (
    InlineError, InlineExpectedIDsError, InlineLostIDError,
    InlineSegment, InlineUnexpectedIDError,
)

SCOPE_ATTRIBUTE = 'inline-scope'


class SiblingInlineScopes:
    def __init__(self, segments: list[InlineSegment], protected_positions: set[int]):
        groups: dict[tuple[int, int], list[InlineSegment]] = {}
        counts = Counter(id(segment.parent) for segment in segments)
        interval = 0
        for index, segment in enumerate(segments):
            if index in protected_positions:
                interval += 1
            if (len(segment.parent_stack) < 2 or counts[id(segment.parent)] != 1
                    or segment.parent.tag == 'expression'):
                continue
            key = id(segment.parent_stack[-2]), interval
            groups.setdefault(key, []).append(segment)
        self._groups = [members for members in groups.values()
                        if len(members) > 1 and any(self._children(member) for member in members)]
        # Identical source tags still need identities once they can change
        # parent/order. Existing document IDs remain source attributes only.
        for members in self._groups:
            for member in members:
                for child in self._children(member):
                    self._identify_subtree(child)
        self._by_block: dict[int, tuple[int, list[InlineSegment], dict[int, InlineSegment]]] = {}

    @staticmethod
    def _children(segment: InlineSegment) -> list[InlineSegment]:
        return [child for child in segment.children if isinstance(child, InlineSegment)]

    @classmethod
    def _identify_subtree(cls, segment: InlineSegment) -> None:
        segment.id = 0
        for child in cls._children(segment):
            cls._identify_subtree(child)

    def bind_ids(self) -> None:
        for scope, members in enumerate(self._groups, 1):
            children = {child.id: child for member in members for child in self._children(member)
                        if child.id is not None}
            for member in members:
                assert member.id is not None
                self._by_block[member.id] = scope, members, children

    def label(self, block_id: int | None) -> str | None:
        entry = self._by_block.get(block_id) if block_id is not None else None
        return str(entry[0]) if entry else None

    def validate(self, block: InlineSegment, candidate: Element,
                 candidates: dict[int, Element]) -> Generator[InlineError | FoundInvalidIDError, None, None]:
        entry = self._by_block.get(block.id) if block.id is not None else None
        if entry is None:
            yield from block.validate(candidate)
            return
        _, members, children = entry
        # A required subtree must occur directly below one of the allowed
        # blocks. A nested occurrence does not count as relocation.
        present = {element_id(node) for member in members
                   if member.id is not None and member.id in candidates
                   for node in candidates[member.id]}
        missing = {child.id: child.parent for child in self._children(block)
                   if child.id not in present and child.id is not None}
        if missing:
            yield InlineExpectedIDsError(missing)
        for node in candidate:
            identity = validate_id_in_element(node)
            if isinstance(identity, FoundInvalidIDError):
                if node.get('id') is None:
                    yield InlineLostIDError(node, [block.parent])
                else:
                    yield identity
                continue
            source = children.get(identity)
            if source is None:
                yield InlineUnexpectedIDError(identity, node)
            elif node.tag != source.parent.tag:
                yield InlineUnexpectedIDError(identity, node)
            else:
                # Descendants stay attached to this root: validate the entire
                # subtree with the unchanged local nesting/identity contract.
                yield from source.validate(node)

    def adopt(self, block: InlineSegment, candidate: Element) -> Element:
        """Bind a validated candidate without restoring its old parent/order."""
        entry = self._by_block.get(block.id) if block.id is not None else None
        if entry is None:
            return block.assign_attributes(candidate)
        children = entry[2]
        result = Element(block.parent.tag, block.parent.attrib)
        result.text = candidate.text
        for node in candidate:
            identity = int(node.get('id', ''))
            restored = children[identity].assign_attributes(node)
            restored.tail = node.tail
            result.append(restored)
        return result
