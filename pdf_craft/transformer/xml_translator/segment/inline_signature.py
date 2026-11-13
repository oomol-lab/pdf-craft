"""Attribute signatures for one source-defined inline exchange domain.

A signature identifies an attribute template, never a particular occurrence.
Equivalent parent occurrences share child attribute templates at the same depth.
"""
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from .inline_segment import InlineSegment

SIGNATURE_ATTRIBUTE = 'signature'


def _attributes(element: Element) -> tuple[tuple[str, str], ...]:
    # Disk-stream bookkeeping is not a document attribute or signature.
    # It must not distinguish otherwise identical formatting occurrences.
    return tuple(sorted((key, value) for key, value in element.attrib.items()
                        if not key.startswith('__pdf_craft_')))


@dataclass
class InlineBinding:
    source: InlineSegment
    candidate: Element
    children: list["InlineBinding"]

    def materialize(self) -> Element:
        # Fresh inline occurrences must stay fresh during detached-task rebinding;
        # copying a source stream-node key could collapse repeated wrappers.
        result = Element(self.candidate.tag, dict(_attributes(self.source.parent)))
        result.text, result.tail = self.candidate.text, self.candidate.tail
        result.extend(child.materialize() for child in self.children)
        return result


@dataclass
class SignatureIssue:
    kind: str
    tag: str
    signature: str | None = None


class InlineSignatureDomain:
    def __init__(self, sources: list[InlineSegment], counter: list[int]):
        self._tags: dict[str, dict[tuple[tuple[str, str], ...], list[InlineSegment]]] = {}
        self._labels: dict[tuple[str, tuple[tuple[str, str], ...]], str | None] = {}
        self._nested: dict[tuple[str, tuple[tuple[str, str], ...]], InlineSignatureDomain] = {}
        for source in sources:
            self._tags.setdefault(source.parent.tag, {}).setdefault(_attributes(source.parent), []).append(source)
        for tag, variants in self._tags.items():
            for attributes in variants:
                label = None
                if len(variants) > 1:
                    counter[0] += 1
                    label = str(counter[0])
                self._labels[tag, attributes] = label
        for tag, variants in self._tags.items():
            for attributes, members in variants.items():
                # Occurrences with the same parent attribute template cannot
                # be distinguished by occurrence. Their child signatures must
                # therefore be consistent across that equivalent parent domain.
                self._nested[tag, attributes] = InlineSignatureDomain(
                    [child for source in members for child in source.children
                     if isinstance(child, InlineSegment)], counter)

    def encode(self, source: InlineSegment) -> Element:
        node = source.create_element()
        self.encode_into(source, node)
        return node

    def encode_into(self, source: InlineSegment, node: Element) -> None:
        node.attrib.pop('id', None)
        label = self._labels[source.parent.tag, _attributes(source.parent)]
        if label is not None:
            node.set(SIGNATURE_ATTRIBUTE, label)
        nested = self._nested[source.parent.tag, _attributes(source.parent)]
        children = [child for child in source.children if isinstance(child, InlineSegment)]
        for child, target in zip(children, node, strict=True):
            nested.encode_into(child, target)

    def resolve(self, nodes: list[Element], *, require_presence: bool = True
                ) -> tuple[list[InlineBinding], list[SignatureIssue]]:
        """Read-only binding; no source attributes/content are written here."""
        restored: list[InlineBinding] = []
        issues: list[SignatureIssue] = []
        groups: dict[tuple[str, tuple[tuple[str, str], ...]], list[Element]] = {}
        for node in nodes:
            label = node.get(SIGNATURE_ATTRIBUTE)
            key = next((key for key, expected in self._labels.items()
                        if key[0] == node.tag and expected == label), None)
            if node.get('id') is not None:
                issues.append(SignatureIssue('unexpected_id', node.tag, label))
            if key is None:
                issues.append(SignatureIssue('unexpected', node.tag, label))
            else:
                groups.setdefault(key, []).append(node)
        by_candidate: dict[int, InlineBinding] = {}
        for key, label in self._labels.items():
            tag, attributes = key
            candidates = groups.get(key, [])
            if not candidates:
                if require_presence:
                    issues.append(SignatureIssue('missing', tag, label))
                continue
            children, errors = self._nested[key].resolve(
                [child for candidate in candidates for child in candidate], require_presence=require_presence)
            issues.extend(errors)
            by_child = {id(binding.candidate): binding for binding in children}
            source = self._tags[tag][attributes][0]
            for node in candidates:
                by_candidate[id(node)] = InlineBinding(
                    source, node, [by_child[id(child)] for child in node if id(child) in by_child])
        restored.extend(by_candidate[id(node)] for node in nodes if id(node) in by_candidate)
        return restored, issues
