from typing import Generator
from xml.etree.ElementTree import Element


def search_xml_children(parent: Element) -> Generator[tuple[Element, Element], None, None]:
  for child in parent:
    yield child, parent
    yield from search_xml_children(child)