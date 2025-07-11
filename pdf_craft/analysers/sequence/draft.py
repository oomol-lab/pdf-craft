from dataclasses import dataclass
from enum import auto, Enum
from xml.etree.ElementTree import Element
from ..data import ParagraphType


class TruncationKind(Enum):
  NO = auto()
  VERIFIED = auto()
  UNCERTAIN = auto()

@dataclass
class _Child:
  page_index: int
  element: Element
  tokens: int
  tail: TruncationKind

class ParagraphDraft:
  def __init__(self, type: ParagraphType, page_index: int):
    self._type: ParagraphType = type
    self._page_index: int = page_index
    self._children: list[_Child] = []

  @property
  def page_index(self) -> int:
    return self._page_index

  @property
  def type(self) -> ParagraphType:
    return self._type

  def append(self, page_index: int, element: Element, tokens: int):
    self._children.append(_Child(
      page_index=page_index,
      element=element,
      tokens=tokens,
      tail=TruncationKind.NO,
    ))

  def set_tail_truncation(self, kind: TruncationKind):
    self._children[-1].tail = kind

  def to_xml(self) -> Element:
    element = Element("paragraph")
    element.set("type", self._type.value)
    for _, child in self._children:
      element.append(child)
    return element