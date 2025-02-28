from io import TextIOWrapper
from enum import IntEnum
from typing import Callable
from dataclasses import dataclass


@dataclass
class TextIncision(IntEnum):
  MUST_BE = 2
  MOST_LIKELY = 1
  IMPOSSIBLE = -1
  UNCERTAIN = 0

@dataclass
class TextInfo:
  page_index: int
  tokens: int
  start_incision: TextIncision
  end_incision: TextIncision

@dataclass
class PageInfo:
  page_index: int
  main: TextInfo
  citation: TextInfo | None
  file: Callable[[], TextIOWrapper]