import os
import re

from typing import Iterable
from io import StringIO
from xml.etree.ElementTree import tostring, Element
from .types import PageInfo, TextInfo, TextIncision, IndexInfo
from .llm import LLM
from .citation import analyse_citations
from .main_text import analyse_main_texts
from .utils import read_xml_files


_SYMBOLS = ("*", "+", "-")
_INTENTS = 2

class SecondaryAnalyser:
  def __init__(self, llm: LLM, dir_path: str):
    self._llm: LLM = llm
    self._assets_dir_path = os.path.join(dir_path, "assets")
    self._pages: list[PageInfo] = []
    self._index: IndexInfo | None = None

    for root, file_name, kind, index1, index2 in read_xml_files(
      dir_path=os.path.join(dir_path, "pages"),
      enable_kinds=("page", "index"),
    ):
      if kind == "page":
        page_index = index1 - 1
        file_path = os.path.join(dir_path, "pages", file_name)
        page = self._parse_page_info(file_path, page_index, root)
        self._pages.append(page)

      elif kind == "index":
        text = self._format_index(root)
        if text is not None:
          self._index = IndexInfo(
            start_page_index=index1 - 1,
            end_page_index=index2 - 1,
            text=text,
          )

    self._pages.sort(key=lambda p: p.page_index)

  def analyse_citations(self, output_dir_path: str, request_max_tokens: int, tail_rate: float):
    for start_idx, end_idx, chunk_xml in analyse_citations(
      llm=self._llm,
      pages=self._pages,
      request_max_tokens=request_max_tokens,
      tail_rate=tail_rate,
    ):
      file_name = f"chunk_{start_idx + 1}_{end_idx + 1}.xml"
      file_path = os.path.join(output_dir_path, file_name)

      with open(file_path, "wb") as file:
        file.write(tostring(chunk_xml, encoding="utf-8"))

  def analyse_main_texts(self, citations_dir_path: str, output_dir_path: str, request_max_tokens: int, gap_rate: float):
    for start_idx, end_idx, chunk_xml in analyse_main_texts(
      llm=self._llm,
      pages=self._pages,
      index=self._index,
      citations_dir_path=citations_dir_path,
      request_max_tokens=request_max_tokens,
      gap_rate=gap_rate,
    ):
      file_name = f"chunk_{start_idx + 1}_{end_idx + 1}.xml"
      file_path = os.path.join(output_dir_path, file_name)

      with open(file_path, "wb") as file:
        file.write(tostring(chunk_xml, encoding="utf-8"))

  def _parse_page_info(self, file_path: str, page_index: int, root: Element) -> PageInfo:
    main_children: list[Element] = []
    citation: TextInfo | None = None

    for child in root:
      if child.tag == "citation":
        citation = self._parse_text_info(page_index, child)
      else:
        main_children.append(child)

    return PageInfo(
      page_index=page_index,
      citation=citation,
      file=lambda: open(file_path, "rb"),
      main=self._parse_text_info(page_index, main_children),
    )

  def _parse_text_info(self, page_index: int, children: Iterable[Element]) -> TextInfo:
    # When no text is found on this page, it means it is full of tables or
    # it is a blank page. We cannot tell if there is a cut in the context.
    start_incision: TextIncision = TextIncision.UNCERTAIN
    end_incision: TextIncision = TextIncision.UNCERTAIN
    first: Element | None = None
    last: Element | None = None

    for child in children:
      if first is None:
        first = child
      last = child

    if first is not None and last is not None:
      if first.tag == "text":
        start_incision = self._attr_value_to_kind(first.attrib.get("start-incision"))
      if last.tag == "text":
        end_incision = self._attr_value_to_kind(last.attrib.get("end-incision"))

    tokens = self._count_elements_tokens(children)

    return TextInfo(
      page_index=page_index,
      tokens=tokens,
      start_incision=start_incision,
      end_incision=end_incision,
    )

  def _count_elements_tokens(self, elements: Iterable[Element]) -> int:
    root = Element("page")
    root.extend(elements)
    xml_content = tostring(root, encoding="unicode")
    return self._llm.count_tokens_count(xml_content)

  def _attr_value_to_kind(self, value: str | None) -> TextIncision:
    if value == "must-be":
      return TextIncision.MUST_BE
    elif value == "most-likely":
      return TextIncision.MOST_LIKELY
    elif value == "impossible":
      return TextIncision.IMPOSSIBLE
    elif value == "uncertain":
      return TextIncision.UNCERTAIN
    else:
      return TextIncision.UNCERTAIN

  def _format_index(self, root: Element) -> str | None:
    prefaces: Element | None = None
    chapters: Element | None = None

    for child in root:
      if child.tag == "prefaces":
        prefaces = child
      elif child.tag == "chapters":
        chapters = child

    if chapters is None:
      if prefaces is None:
        return None
      chapters = prefaces

    buffer = StringIO()
    if prefaces is None:
      self._write_chapters(buffer, chapters, 0)
    else:
      buffer.write("### Prefaces\n\n")
      self._write_chapters(buffer, prefaces, 0)
      buffer.write("\n\n")
      buffer.write("### Chapters\n\n")
      self._write_chapters(buffer, chapters, 0)

    return buffer.getvalue()

  def _write_chapters(self, buffer: StringIO, chapters: Element, level: int):
    for chapter in chapters:
      if chapter.tag != "chapter":
        continue

      headline = chapter.find("headline")
      children = chapter.find("children")
      if headline is None:
        continue

      for _ in range(level * _INTENTS):
        buffer.write(" ")

      symbol = _SYMBOLS[level % len(_SYMBOLS)]
      text: str = re.sub(r"\s+", " ", headline.text)
      text = text.strip()

      buffer.write(symbol)
      buffer.write(" ")
      buffer.write(text)
      buffer.write("\n")

      if children is not None:
        self._write_chapters(buffer, children, level + 1)
