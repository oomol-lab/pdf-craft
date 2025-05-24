from pathlib import Path
from typing import Generator
from ..sequence import read_paragraphs
from ..data import Paragraph


class ParagraphsReader:
  def __init__(self, from_path: Path):
    self._gen: Generator[Paragraph, None, None] = read_paragraphs(from_path)
    self._buffer: tuple[tuple[int, int], Paragraph] | None = None

  def read(self, page_index: int, order_index: int) -> Paragraph | None:
    index = (page_index, order_index)
    while True:
      if self._buffer is None:
        self._forward(index)
        if self._buffer is None:
          return None # read to the end

      found_index, paragraph = self._buffer
      if found_index < index:
        self._forward(index)
      elif found_index > index:
        return None
      else:
        return paragraph

  def _forward(self, index: tuple[int, int]) -> None:
    while True:
      paragraph = next(self._gen, None)
      if paragraph is None:
        self._buffer = None
        break

      found_index = (paragraph.page_index, paragraph.order_index)
      if found_index >= index:
        self._buffer = (found_index, paragraph)
        break