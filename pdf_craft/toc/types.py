from dataclasses import dataclass
from xml.etree.ElementTree import Element

from ..common import indent


@dataclass
class Toc:
    id: int
    page_index: int
    order: int
    level: int
    children: list["Toc"]


def encode(toc_list: list[Toc]) -> Element:
    root = Element("toc")

    def encode_item(toc: Toc, parent: Element) -> None:
        item = Element("item")
        item.set("id", str(toc.id))
        item.set("page_index", str(toc.page_index))
        item.set("order", str(toc.order))
        item.set("level", str(toc.level))

        for child in toc.children:
            encode_item(child, item)
        parent.append(item)

    for toc in toc_list:
        encode_item(toc, root)

    return indent(root)


def decode(element: Element) -> list[Toc]:
    if element.tag != "toc":
        raise ValueError(f"Expected root tag 'toc', got '{element.tag}'")

    def decode_item(item: Element) -> Toc:
        if item.tag != "item":
            raise ValueError(f"Expected tag 'item', got '{item.tag}'")

        id_str = item.get("id")
        page_index_str = item.get("page_index")
        order_str = item.get("order")
        level_str = item.get("level")

        if id_str is None:
            raise ValueError("Missing 'id' attribute in item")
        if page_index_str is None:
            raise ValueError("Missing 'page_index' attribute in item")
        if order_str is None:
            raise ValueError("Missing 'order' attribute in item")
        if level_str is None:
            raise ValueError("Missing 'level' attribute in item")

        children = [decode_item(child) for child in item]

        return Toc(
            id=int(id_str),
            page_index=int(page_index_str),
            order=int(order_str),
            level=int(level_str),
            children=children,
        )
    return [decode_item(item) for item in element]
