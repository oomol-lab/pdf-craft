from dataclasses import dataclass
from typing import Callable, Generator, Generic, Iterable, TypeVar
from xml.etree.ElementTree import Element

from .tags import HTMLTagDefinition, tag_definition

P = TypeVar("P")


@dataclass
class HTMLTag(Generic[P]):
    definition: HTMLTagDefinition
    attributes: list[tuple[str, str]]
    children: list["str | P | HTMLTag[P]"]


def flatten(children: Iterable[str | P | HTMLTag[P]]) -> Generator[str | P, None, None]:
    for child in children:
        if isinstance(child, HTMLTag):
            yield from flatten(child.children)
        else:
            yield child


def decode(
    root: Element, decode_payload: Callable[[Element], P]
) -> list[str | P | HTMLTag[P]]:
    children: list[str | P | HTMLTag[P]] = []
    if root.text:
        children.append(root.text)

    for child in root:
        tag_def = tag_definition(child.tag)
        if tag_def is not None:
            attributes = list(child.attrib.items())
            children.append(
                HTMLTag(
                    definition=tag_def,
                    attributes=attributes,
                    children=decode(child, decode_payload),
                )
            )
        else:
            children.append(decode_payload(child))

        if child.tail:
            children.append(child.tail)

    return children


def encode(
    root: Element,
    children: list[str | P | HTMLTag[P]],
    encode_payload: Callable[[P], Element],
) -> None:
    if not children:
        return

    last_element: Element | None = None
    for child in children:
        if isinstance(child, str):
            if last_element is None:
                root.text = (root.text or "") + child
            else:
                last_element.tail = (last_element.tail or "") + child
        elif isinstance(child, HTMLTag):
            element = Element(child.definition.name, dict(child.attributes))
            encode(element, child.children, encode_payload)
            root.append(element)
            last_element = element
        else:
            element = encode_payload(child)
            root.append(element)
            last_element = element
