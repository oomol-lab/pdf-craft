from __future__ import annotations
from dataclasses import dataclass
from strenum import StrEnum


@dataclass
class Paragraph:
  type: ParagraphType
  page_index: int
  order_index: int
  layouts: list[Layout]

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
  lines: list[Line]

  @property
  def id(self) -> str:
    return f"{self.page_index}/{self.order_index}"

class LayoutKind(StrEnum):
  TEXT = "text"
  HEADLINE = "headline"
  FIGURE = "figure"
  TABLE = "table"
  FORMULA = "formula"
  ABANDON = "abandon"

@dataclass
class Line:
  text: str
  confidence: str