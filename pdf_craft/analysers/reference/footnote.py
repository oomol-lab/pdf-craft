from __future__ import annotations
from pathlib import Path
from shutil import rmtree
from typing import Generator, Iterator
from xml.etree.ElementTree import Element

from ...xml import encode
from ..data import Layout
from ..sequence import decode_layout
from ..chapter import list_chapter_files
from ..utils import xml_files, read_xml_file, XML_Info
from .extraction import extract_footnote_references, ExtractedFootnote
from .mark import transform2mark, search_marks, Mark


def append_footnote_for_chapters(chapter_path: Path, footnote_path: Path, output_path: Path) -> None:
  if output_path.exists():
    rmtree(output_path)

  output_path.mkdir(parents=True, exist_ok=True)
  footnote_reader = _FootnoteReader(footnote_path)

  for chapter_id, file_path in list_chapter_files(chapter_path):
    chapter_element = Element("chapter")
    chapter_footnotes: list[tuple[int, int, Element, Element]] = []

    for raw_layout_element in read_xml_file(file_path):
      layout_element: Layout = raw_layout_element
      layout = decode_layout(raw_layout_element)
      footnote_page = footnote_reader.read(layout.page_index)
      if footnote_page is not None:
        layout_element, layout_footnotes = _parse_layout_and_mark(
          footnote_page=footnote_page,
          layout=layout,
        )
        for id, mark_element, footnote_element in layout_footnotes:
          chapter_footnotes.append((layout.page_index, id, mark_element, footnote_element))
      chapter_element.append(layout_element)

    next_mark_id: int = 1
    for _, _, mark_element, footnote_element in sorted(
      chapter_footnotes,
      key=lambda x: (x[0], x[1]),
    ):
      footnote_element.set("id", str(next_mark_id))
      mark_element.set("id", str(next_mark_id))
      chapter_element.append(footnote_element)
      next_mark_id += 1

    if chapter_id is None:
      file_path = output_path / "chapter_head.xml"
    else:
      file_path = output_path / f"chapter_{chapter_id}.xml"

    with open(file_path, "w", encoding="utf-8") as file:
      file.write(encode(chapter_element))

def _parse_layout_and_mark(footnote_page: _FootnotePage, layout: Layout):
  layout_element = layout.to_xml()
  footnotes: list[tuple[int, Element, Element]] = []

  for line_element in layout_element:
    raw_text = line_element.text.strip()
    line_element.text = None
    last_mark_element: Element | None = None

    for cell in search_marks(raw_text):
      is_footnote_mark = False
      if isinstance(cell, Mark):
        footnote_element = footnote_page.pop(cell)
        if footnote_element is not None:
          footnote_id = int(footnote_element.get("id", "-1"))
          mark_element = Element("mark")
          mark_element.text = str(cell)
          line_element.append(mark_element)
          footnotes.append((footnote_id, mark_element, footnote_element))
          is_footnote_mark = True
          last_mark_element = mark_element

      if not is_footnote_mark:
        if last_mark_element is None:
          if line_element.text:
            line_element.text += str(cell)
          else:
            line_element.text = str(cell)
        elif last_mark_element.tail:
          last_mark_element.tail += str(cell)
        else:
          last_mark_element.tail = str(cell)

    return layout_element, footnotes

class _FootnoteReader:
  def __init__(self, footnote_path: Path):
    self._infos_iter: Iterator[XML_Info] = iter(xml_files(footnote_path))
    self._buffer_info: XML_Info | None = None
    self.buffer_page: _FootnotePage | None = None

  def read(self, page_index: int) -> _FootnotePage | None:
    while True:
      if self._buffer_info is None:
        self._forward_buffer_info(page_index)
        if self._buffer_info is None:
          return None # read to the end

      file_path, _, file_page_index, _ = self._buffer_info
      if file_page_index < page_index:
        self._forward_buffer_info(page_index)
      elif file_page_index > page_index:
        return None
      else:
        if self.buffer_page is None:
          self.buffer_page = _FootnotePage(read_xml_file(file_path))
        return self.buffer_page

  def _forward_buffer_info(self, page_index: int):
    while True:
      info = next(self._infos_iter, None)
      if info is None:
        self._buffer_info = None
        self.buffer_page = None
        break
      _, _, file_page_index, _ = info
      if file_page_index >= page_index:
        self._buffer_info = info
        self.buffer_page = None
        break

class _FootnotePage:
  def __init__(self, element: Element):
    self._mark2element: dict[Mark, Element] = {}
    for footnote in element:
      if footnote.tag != "footnote":
        continue
      mark_element = footnote.find("mark")
      if mark_element is None:
        continue
      mark = transform2mark(mark_element.text.strip())
      if mark is None:
        continue
      self._mark2element[mark] = footnote

  def pop(self, mark: Mark) -> Element | None:
    return self._mark2element.pop(mark, None)

def generate_footnote_references(sequence_path: Path, output_path: Path) -> None:
  if output_path.exists():
    rmtree(output_path)
  output_path.mkdir(parents=True, exist_ok=True)

  # TODO: 检查每页的内容形式上是否完整，若有问题，交给 LLM 解决
  for page_index, buffer in _extract_and_split_by_pages(sequence_path):
    page_element = Element("page")
    page_element.set("page-index", str(page_index))
    next_footnote_id: int = 1

    for mark, layouts in buffer:
      if mark is None:
        continue # TODO: 这种应该由 LLM 来处理，没实现之前只能跳过
      footnote_element = Element("footnote")
      footnote_element.set("id", str(next_footnote_id))
      next_footnote_id += 1

      mark_element = Element("mark")
      mark_element.text = mark.char
      footnote_element.append(mark_element)
      page_element.append(footnote_element)

      for layout in layouts:
        footnote_element.append(layout.to_xml())

    if len(page_element) > 0:
      file_path = output_path / f"page_{page_index}.xml"
      with open(file_path, "w", encoding="utf-8") as file:
        file.write(encode(page_element))

def _extract_and_split_by_pages(sequence_path: Path) -> Generator[tuple[int, list[ExtractedFootnote]], None, None]:
  page_index = -1
  buffer: list[ExtractedFootnote] = []

  for mark, layouts in extract_footnote_references(sequence_path):
    current_page_index = layouts[0].page_index
    if page_index != current_page_index:
      if buffer:
        yield page_index, buffer
        buffer = []
      page_index = current_page_index
    buffer.append((mark, layouts))

  if buffer:
    yield page_index, buffer