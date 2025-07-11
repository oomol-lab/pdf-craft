from ..data import ParagraphType
from xml.etree.ElementTree import Element


class ParagraphDraft:
  def __init__(self, type: ParagraphType, page_index: int, element: Element):
    self._type: ParagraphType = type
    self._page_index: int = page_index
    self._children: list[tuple[int, Element]] = [(page_index, element)]

  @property
  def page_index(self) -> int:
    return self._page_index

  @property
  def type(self) -> ParagraphType:
    return self._type

  def append(self, page_index: int, element: Element):
    self._children.append((page_index, element))

  def to_xml(self) -> Element:
    element = Element("paragraph")
    element.set("type", self._type.value)
    for _, child in self._children:
      element.append(child)
    return element