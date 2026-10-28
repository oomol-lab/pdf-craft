"""Source identity matching shared by fill recovery and attribute adoption."""
from collections.abc import Iterable, Iterator
from typing import TypeVar
from xml.etree.ElementTree import Element

T = TypeVar('T')


def element_id(element: Element) -> int | None:
    try:
        return int(element.get('id', ''))
    except ValueError:
        return None


def match_children(sources: Iterable[tuple[str, int | None, T]],
                   target: Element) -> Iterator[tuple[T, Element]]:
    """Match by tag and ID first, then by occurrence within that tag.

    Different tags may reorder. Return matches in the target's reading order,
    exactly as the attribute adopter requires. Validation owns missing IDs/tags.
    """
    groups: dict[str, list[tuple[int | None, T]]] = {}
    for tag, identifier, source in sources:
        groups.setdefault(tag, []).append((identifier, source))
    used_ids: set[int] = set()
    assigned: dict[int, T] = {}
    for tag, members in groups.items():
        identities = [identifier for identifier, _ in members]
        matched: list[Element | None] = [None] * len(members)
        remaining = []
        for child in target:
            if child.tag != tag:
                continue
            identifier = element_id(child)
            order = None
            if identifier is not None and identifier not in used_ids:
                used_ids.add(identifier)
                if identifier in identities:
                    order = identities.index(identifier)
            if order is None:
                remaining.append(child)
            else:
                matched[order] = child
        pending = iter(remaining)
        for index, child in enumerate(matched):
            if child is None:
                matched[index] = next(pending, None)
        for (_, source), child in zip(members, matched):
            if child is not None:
                assigned[id(child)] = source
    for child in target:
        if id(child) in assigned:
            yield assigned[id(child)], child
