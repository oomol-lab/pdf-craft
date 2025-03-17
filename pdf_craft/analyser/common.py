from io import TextIOWrapper
from typing import Callable
from dataclasses import dataclass
from resource_segmentation import Resource


@dataclass
class PageRef:
  page_index: int

@dataclass
class PageInfo:
  page_index: int
  main: Resource[PageRef]
  citation: Resource[PageRef] | None
  file: Callable[[], TextIOWrapper]