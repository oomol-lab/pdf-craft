from collections.abc import Generator
from xml.etree.ElementTree import Element


def find_first(element: Element, tag: str) -> Element | None:
    if element.tag == tag:
        return element
    for child in element:
        result = find_first(child, tag)
        if result is not None:
            return result
    return None


def index_in_parent(parent: Element, element: Element) -> int | None:
    for i, child in enumerate(parent):
        if child is element:
            return i
    return None


def iter_with_stack(element: Element) -> Generator[tuple[list[Element], Element], None, None]:
    """先序遍历：yield parent_path, element"""
    stack: list[list[Element]] = [[element]]
    while stack:
        current_path = stack.pop()
        current = current_path[-1]
        yield current_path[:-1], current

        if len(current) == 0:
            continue

        for child in reversed(list(current)):
            child_path = list(current_path)
            child_path.append(child)
            stack.append(child_path)


def clone_element(element: Element) -> Element:
    new_element = Element(element.tag, element.attrib)
    new_element.text = element.text
    for child in element:
        new_child = clone_element(child)
        new_child.tail = child.tail
        new_element.append(new_child)
    return new_element


def plain_text(element: Element) -> str:
    return "".join(_iter_text_in(element))


def _iter_text_in(element: Element) -> Generator[str, None, None]:
    if element.text:
        yield element.text
    for child in element:
        yield from _iter_text_in(child)
        if child.tail:
            yield child.tail
