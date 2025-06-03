import sys

from pathlib import Path
from typing import Generator, TypedDict
from strenum import StrEnum
from abc import ABC, abstractmethod
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..sequence import read_paragraphs
from ..data import Paragraph, ParagraphType, AssetLayout, FormulaLayout
from ..utils import Context, MultiThreads


class Phase(StrEnum):
  Text = "text"
  FOOTNOTE = "footnote"
  GENERATION = "generation"
  COMPLETED = "completed"

class Level(StrEnum):
  Single = "single"
  Multiple = "multiple"

class State(TypedDict):
  phase: Phase
  level: Level
  max_data_tokens: int
  completed_ranges: list[list[int]]

class Corrector(ABC):
  def __init__(self, llm: LLM, context: Context[State], threads: MultiThreads):
    self.llm: LLM = llm
    self.ctx: Context[State] = context
    self.threads: MultiThreads = threads

  @abstractmethod
  def do(self, from_path: Path, request_path: Path, is_footnote: bool) -> None:
    raise NotImplementedError()

  def extract_lines(self, extracted_element: Element) -> Generator[tuple[tuple[int, int], list[str]], None, None]:
    for layout in extracted_element:
      layout_id = layout.get("id", None)
      if layout_id is None:
        continue
      id1, id2 = layout_id.split("/", maxsplit=1)
      index = (int(id1), int(id2))
      lines: list[str] = []

      for line in layout:
        if line.tag != "line":
          continue
        line_id = line.get("id", None)
        if line_id is None:
          continue
        if line.text:
          lines.append(line.text.strip())
        else:
          lines.append("")

      yield index, lines

  def generate_request_xml(self, from_path: Path) -> Generator[tuple[tuple[int, int], tuple[int, int], Element], None, None]:
    max_data_tokens = self.ctx.state["max_data_tokens"]
    request_element = Element("request")
    request_begin: tuple[int, int] = (sys.maxsize, sys.maxsize)
    request_end: tuple[int, int] = (-1, -1)
    data_tokens: int = 0
    last_type: ParagraphType | None = None

    for paragraph in read_paragraphs(from_path):
      layout_element = self._paragraph_to_layout_xml(paragraph)
      tokens = self.llm.count_tokens_count(
        text=encode_friendly(layout_element),
      )
      if len(request_element) > 0 and (
        data_tokens + tokens > max_data_tokens or
        last_type != paragraph.type
      ):
        yield request_begin, request_end, request_element
        request_element = Element("request")
        data_tokens = 0
        request_begin = (sys.maxsize, sys.maxsize)
        request_end = (-1, -1)

      paragraph_index = (paragraph.page_index, paragraph.order_index)
      request_element.append(layout_element)
      request_begin = min(request_begin, paragraph_index)
      request_end = max(request_end, paragraph_index)
      data_tokens += tokens
      last_type = paragraph.type

    if len(request_element) > 0:
      yield request_begin, request_end, request_element

  def _paragraph_to_layout_xml(self, paragraph: Paragraph) -> tuple[int, Element]:
    layout_element: Element | None = None
    next_line_id: int = 1

    for layout in paragraph.layouts:
      if layout_element is None:
        layout_element = Element(layout.kind.value)
        layout_element.set("id", layout.id)

      if isinstance(layout, AssetLayout):
        line_element = Element("line")
        line_element.set("id", str(object=next_line_id))
        next_line_id += 1
        layout_element.append(line_element)

        if isinstance(layout, FormulaLayout) and layout.latex:
          line_element.text = layout.latex.strip()
        else:
          line_element.text = f"[[here is a {layout.kind.value}]]"

      else:
        for line in layout.lines:
          line_element = Element("line")
          line_element.set("id", str(next_line_id))
          line_element.text = line.text.strip()
          layout_element.append(line_element)
          next_line_id += 1

    assert layout_element is not None
    return layout_element
