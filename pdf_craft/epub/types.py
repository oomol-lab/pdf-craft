from enum import auto, Enum


class TableRender(Enum):
  HTML = auto()
  CLIPPING = auto()

class LaTeXRender(Enum):
  MATHML = auto()
  SVG = auto()
  CLIPPING = auto()
