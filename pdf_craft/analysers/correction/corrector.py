import sys
import shutil

from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..utils import Context, Partition
from ..sequence import read_paragraphs
from ..data import Paragraph, ParagraphType, AssetLayout, FormulaLayout
from .common import State
from .repeater import repeat_correct
from .paragraphs_reader import ParagraphsReader


class Corrector:
  def __init__(self, llm: LLM, context: Context[State]):
    self._llm: LLM = llm
    self._ctx: Context[State] = context

  def do(self, from_path: Path, request_path: Path, is_footnote: bool):
    request_path.mkdir(parents=True, exist_ok=True)
    reader = ParagraphsReader(from_path)
    partition: Partition[tuple[int, int], State, Element] = Partition(
      dimension=2,
      context=self._ctx,
      sequence=self._generate_request_xml(from_path),
      remove=lambda begin, end: shutil.rmtree(
        request_path / _file_name("steps", begin, end),
      ),
    )
    with partition:
      for task in partition.pop_tasks():
        with task:
          begin = task.begin
          end = task.end
          request_element = task.payload
          resp_element = repeat_correct(
            llm=self._llm,
            context=self._ctx,
            save_path=request_path / _file_name("steps",begin, end),
            raw_request=request_element,
            is_footnote=is_footnote,
          )
          self._apply_updation(
            reader=reader,
            request_path=request_path,
            request_element=request_element,
            resp_element=resp_element,
          )

  def _generate_request_xml(self, from_path: Path):
    max_data_tokens = self._ctx.state["max_data_tokens"]
    request_element = Element("request")
    request_begin: tuple[int, int] = (sys.maxsize, sys.maxsize)
    request_end: tuple[int, int] = (-1, -1)
    data_tokens: int = 0
    last_type: ParagraphType | None = None

    for paragraph in read_paragraphs(from_path):
      layout_element = self._paragraph_to_layout_xml(paragraph)
      tokens = self._llm.count_tokens_count(
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

  def _apply_updation(
        self,
        request_path: Path,
        reader: ParagraphsReader,
        request_element: Element,
        resp_element: Element,
      ) -> None:

    raw_lines_list = list(self._extract_lines(request_element))
    corrected_lines_dict = dict(self._extract_lines(resp_element))
    chunk_element = Element("chunk")

    for index, raw_lines in sorted(raw_lines_list, key=lambda x: x[0]):
      page_index, order_index = index
      paragraph = reader.read(
        layout_index=(page_index, order_index),
      )
      if paragraph is None:
        continue
      corrected_lines = corrected_lines_dict.get(index, None)
      if corrected_lines is None:
        corrected_lines = raw_lines
      paragraph = self._apply_paragraph_lines(
        paragraph=paragraph,
        lines=corrected_lines,
      )
      if paragraph is not None:
        paragraph_element = paragraph.to_xml()
        paragraph_element.set("page-index", str(page_index))
        paragraph_element.set("order-index", str(order_index))
        chunk_element.append(paragraph_element)

    if not raw_lines_list:
      return

    begin, _ = raw_lines_list[0]
    end, _ = raw_lines_list[-1]
    file_name = _file_name("chunk", begin, end) + ".xml"
    self._ctx.write_xml_file(
      file_path=request_path / file_name,
      xml=chunk_element,
    )

  def _extract_lines(self, extracted_element: Element) -> Generator[tuple[int, Element], None, None]:
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

  def _apply_paragraph_lines(self, paragraph: Paragraph, lines: list[str]) -> Paragraph | None:
    next_line_index: int = 0
    limit_length: int = -1

    for i, layout in enumerate(paragraph.layouts):
      for j, line in enumerate(layout.lines):
        if next_line_index >= len(lines):
          limit_length = j
          break
        line.text = lines[next_line_index]
        next_line_index += 1

      if limit_length < 0:
        continue
      if isinstance(layout, AssetLayout):
        continue

      layout.lines = layout.lines[:limit_length]
      if layout.lines:
        limit_length = i + 1
      else:
        limit_length = i

    if limit_length != -1:
      paragraph.layouts = paragraph.layouts[:limit_length]
    if not paragraph.layouts:
      return None

    return paragraph

def _file_name(name: str, begin: tuple[int, int], end: tuple[int, int]) -> str:
  return f"{name}_{begin[0]}_{begin[1]}_{end[0]}_{end[1]}"