"""Request-local relocation of inline subtrees between real sibling blocks.

The transport root can aggregate unrelated chapters. Only source-tree siblings
inside one protected-resource interval share a scope; it never defines one.
"""
from collections import Counter
from collections.abc import Generator
from xml.etree.ElementTree import Element

from jinja2 import Environment, PackageLoader

from .common import FoundInvalidIDError
from .inline_segment import InlineError, InlineSegment
from .inline_signature import InlineSignatureDomain

SCOPE_ATTRIBUTE = 'inline-scope'
_FEEDBACK = Environment(loader=PackageLoader("pdf_craft.transformer.xml_translator", "data"), autoescape=False)


class SiblingInlineScopes:
    def __init__(self, segments: list[InlineSegment], protected_positions: set[int]):
        groups: dict[tuple[int, int], list[InlineSegment]] = {}
        counts = Counter(id(segment.parent) for segment in segments)
        interval = 0
        for index, segment in enumerate(segments):
            if index in protected_positions:
                interval += 1
            # Split blocks and independent formula payloads remain local.
            if (len(segment.parent_stack) < 2 or counts[id(segment.parent)] != 1
                    or segment.parent.tag == 'expression'):
                key = id(segment), -1
            else:
                key = id(segment.parent_stack[-2]), interval
            groups.setdefault(key, []).append(segment)
        self._groups = list(groups.values())
        self._domains: dict[int, tuple[list[InlineSegment], InlineSignatureDomain]] = {}
        counter = [0]
        for members in self._groups:
            domain = InlineSignatureDomain(
                [child for member in members for child in self._children(member)], counter)
            for member in members:
                self._domains[id(member)] = members, domain
                for child in self._children(member):
                    self._clear_ids(child)
        self._labels: dict[int, str] = {}

    @staticmethod
    def _children(segment: InlineSegment) -> list[InlineSegment]:
        return [child for child in segment.children if isinstance(child, InlineSegment)]

    @classmethod
    def _clear_ids(cls, segment: InlineSegment) -> None:
        segment.id = None
        for child in cls._children(segment):
            cls._clear_ids(child)

    def bind_ids(self) -> None:
        for scope, members in enumerate(self._groups, 1):
            if len(members) > 1 and any(self._children(member) for member in members):
                for member in members:
                    assert member.id is not None
                    self._labels[member.id] = str(scope)

    def label(self, block_id: int | None) -> str | None:
        return self._labels.get(block_id) if block_id is not None else None

    def create_element(self, block: InlineSegment) -> Element:
        node = block.create_element()
        domain = self._domains[id(block)][1]
        for index, child in enumerate(self._children(block)):
            replacement = domain.encode(child)
            replacement.tail = node[index].tail
            node[index] = replacement
        return node

    def validate(self, block: InlineSegment, candidate: Element,
                 candidates: dict[int, Element]) -> Generator[InlineError | FoundInvalidIDError, None, None]:
        members, domain = self._domains[id(block)]
        # Validate the whole exchange domain once, including empty members.
        if block is not members[0]:
            return
        nodes = [node for member in members if member.id in candidates
                 for node in candidates[member.id]]
        _, issues = domain.resolve(nodes)
        for issue in issues:
            reason = _FEEDBACK.get_template('inline_signature_error.jinja').render(
                issue=issue, scope=self.label(block.id), block_id=block.id).strip()
            yield FoundInvalidIDError(None, candidate, reason)

    def adopt(self, block: InlineSegment, candidate: Element) -> Element:
        domain = self._domains[id(block)][1]
        # Coverage is a domain-wide obligation, already checked before adoption.
        # A particular block may hold none of a required tag/signature.
        children, issues = domain.resolve(list(candidate), require_presence=False)
        if issues:
            raise ValueError('Accepted inline signature cannot be restored')
        result = Element(block.parent.tag, block.parent.attrib)
        result.text = candidate.text
        result.extend(child.materialize() for child in children)
        return result
