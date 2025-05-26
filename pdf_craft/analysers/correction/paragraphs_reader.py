import sys

from pathlib import Path
from typing import Generator
from dataclasses import dataclass

from ..sequence import read_paragraphs
from ..data import Paragraph


@dataclass
class _Buffer:
  paragraph: Paragraph
  paragraph_index: tuple[int, int]
  begin_layout_index: tuple[int, int]
  end_layout_index: tuple[int, int]

  def include(self, layout_index: tuple[int, int]) -> bool:
    return self.begin_layout_index <= layout_index <= self.end_layout_index

class ParagraphsReader:
  def __init__(self, from_path: Path):
    self._gen: Generator[Paragraph, None, None] = read_paragraphs(from_path)
    self._buffer: _Buffer | None = None

  def read(self, layout_index: tuple[int, int]) -> Paragraph | None:
    if self._buffer is None:
      self._forward(layout_index)
    while True:
      if self._buffer is None:
        return None # read to the end
      if layout_index > self._buffer.end_layout_index:
        self._forward(layout_index)
      elif layout_index >= self._buffer.begin_layout_index:
        return self._buffer.paragraph
      else:
        return None

  def _forward(self, layout_index: tuple[int, int]) -> None:
    # Note: paragraph index and layout index are different, and you cannot
    #       assume that there is a certain correspondence between them.
    while True:
      paragraph = next(self._gen, None)
      if paragraph is None:
        self._buffer = None
        break

      if len(paragraph.layouts) == 0:
        continue

      begin_layout_index: tuple[int, int] = (sys.maxsize, sys.maxsize)
      end_layout_index: tuple[int, int] = (-1, -1)

      for layout in paragraph.layouts:
        index = (layout.page_index, layout.order_index)
        if index < begin_layout_index:
          begin_layout_index = index
        if index > end_layout_index:
          end_layout_index = index

      if layout_index <= end_layout_index:
        self._buffer = _Buffer(
          paragraph=paragraph,
          paragraph_index=(paragraph.page_index, paragraph.order_index),
          begin_layout_index=begin_layout_index,
          end_layout_index=end_layout_index,
        )
        break