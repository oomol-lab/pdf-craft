from __future__ import annotations
from dataclasses import dataclass
from strenum import StrEnum
from xml.etree.ElementTree import Element


@dataclass
class Paragraph:
  type: ParagraphType
  page_index: int
  order_index: int
  layouts: list[Layout]

  def xml(self) -> Element:
    element = Element("paragraph")
    element.set("type", self.type.value)
    for layout in self.layouts:
      element.append(layout.to_xml())
    return element

class ParagraphType(StrEnum):
  TEXT = "text"
  CONTENTS = "contents"
  REFERENCES = "references"
  COPYRIGHT = "copyright"

@dataclass
class Layout:
  kind: LayoutKind
  page_index: int
  order_index: int
  caption: Caption
  lines: list[Line]

  @property
  def id(self) -> str:
    return f"{self.page_index}/{self.order_index}"

  def to_xml(self) -> Element:
    element = Element(self.kind.value)
    element.set("id", self.id)
    for line in self.lines:
      element.append(line.to_xml())
    return element

@dataclass
class AssetLayout(Layout):
  hash: bytes

@dataclass
class Formula(AssetLayout):
  latex: str

class LayoutKind(StrEnum):
  TEXT = "text"
  HEADLINE = "headline"
  FIGURE = "figure"
  TABLE = "table"
  FORMULA = "formula"
  ABANDON = "abandon"

@dataclass
class Caption:
  lines: list[Line]

@dataclass
class Line:
  text: str
  confidence: str

  def to_xml(self) -> Element:
    element = Element("line")
    element.text = self.text
    element.set("confidence", str(object=self.confidence))
    return element