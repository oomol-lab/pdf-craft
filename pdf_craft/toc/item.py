from dataclasses import dataclass
from typing import Iterable
from xml.etree.ElementTree import Element

from ..common import indent


@dataclass
class TocItem:
    id: int
    title: str
    children: list["TocItem"]


def decode(element: Element) -> list[TocItem]:
    if element.tag != "toc":
        raise ValueError(f"Expected <toc> element, got <{element.tag}>")

    items: list[TocItem] = []
    for item_el in element.findall("toc-item"):
        items.append(_decode_item(item_el))

    return items


def encode(items: Iterable[TocItem]) -> Element | None:
    root = Element("toc")
    for item in items:
        root.append(_encode_item(item))
    if len(root) == 0:
        return None
    return indent(root)


def _decode_item(element: Element) -> TocItem:
    id_attr = element.get("id")
    if id_attr is None:
        raise ValueError("<toc-item> missing required attribute 'id'")
    try:
        item_id = int(id_attr)
    except ValueError as e:
        raise ValueError(f"<toc-item> attribute 'id' must be int, got: {id_attr}") from e

    title_attr = element.get("title")
    if title_attr is None:
        raise ValueError("<toc-item> missing required attribute 'title'")

    children: list[TocItem] = []
    for child_el in element.findall("toc-item"):
        children.append(_decode_item(child_el))

    return TocItem(
        id=item_id,
        title=title_attr,
        children=children,
    )


def _encode_item(item: TocItem) -> Element:
    el = Element("toc-item")
    el.set("id", str(item.id))
    el.set("title", item.title)

    for child in item.children:
        el.append(_encode_item(child))

    return el
