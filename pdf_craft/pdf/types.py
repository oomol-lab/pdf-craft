from typing import Callable
from enum import auto, Enum
from dataclasses import dataclass
from PIL.Image import Image
from doc_page_extractor import Rectangle


# func(completed_pages: int, all_pages: int) -> None
PDFPageExtractorProgressReport = Callable[[int, int], None]

class OCRLevel(Enum):
  Once = auto()
  OncePerLayout = auto()

class ExtractedTableFormat(Enum):
  LATEX = auto()
  MARKDOWN = auto()
  HTML = auto()
  DISABLE = auto()

class TextKind(Enum):
  TITLE = 0
  PLAIN_TEXT = 1
  ABANDON = 2

@dataclass
class Text:
  content: str
  rank: float
  rect: Rectangle

@dataclass
class BasicBlock:
  rect: Rectangle
  texts: list[Text]
  font_size: float

@dataclass
class TextBlock(BasicBlock):
  kind: TextKind
  has_paragraph_indentation: bool = False
  last_line_touch_end: bool = False

class TableFormat(Enum):
  LATEX = auto()
  MARKDOWN = auto()
  HTML = auto()
  UNRECOGNIZABLE = auto()

@dataclass
class TableBlock(BasicBlock):
  content: str
  format: TableFormat
  image: Image

@dataclass
class FormulaBlock(BasicBlock):
  content: str | None
  image: Image

@dataclass
class FigureBlock(BasicBlock):
  image: Image

AssetBlock = TableBlock | FormulaBlock | FigureBlock
Block = TextBlock | AssetBlock