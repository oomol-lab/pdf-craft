import os
import re

from enum import IntEnum
from typing import Iterable
from dataclasses import dataclass
from natsort import natsorted
from xml.etree.ElementTree import fromstring, Element
from .llm import LLM


@dataclass
class TextIncision(IntEnum):
  MUST_BE = 2
  MOST_LIKELY = 1
  IMPOSSIBLE = -1
  UNCERTAIN = 0

@dataclass
class TextInfo:
  tokens: int
  start_incision: TextIncision
  end_incision: TextIncision

@dataclass
class PageInfo:
  file_name: str
  page_index: int
  main: TextInfo
  citation: TextInfo | None

class SecondaryAnalyser:
  def __init__(self, llm: LLM, dir_path: str):
    self._llm: LLM = llm
    self._assets_dir_path = os.path.join(dir_path, "assets")
    self._pages: list[PageInfo] = []
    pages_dir_path: str = os.path.join(dir_path, "pages")
    file_names = natsorted(os.listdir(pages_dir_path))
    file_names = [f for f in file_names if re.match(r"^page_\d+\.xml$", f)]

    for page_index, file_name in enumerate(file_names):
      file_path = os.path.join(pages_dir_path, file_name)
      with open(file_path, "r", encoding="utf-8") as file:
        root: Element = fromstring(file.read())
        page = self._parse_xml(file_name, page_index, root)
        self._pages.append(page)

  def _parse_xml(self, file_name: str, page_index: int, root: Element) -> PageInfo:
    main_children: list[Element] = []
    citation: TextInfo | None = None

    for child in root:
      if child.tag == "citation":
        citation = self._parse_text_info(child)
      else:
        main_children.append(child)

    return PageInfo(
      file_name=file_name,
      page_index=page_index,
      citation=citation,
      main=self._parse_text_info(main_children),
    )

  def _parse_text_info(self, children: Iterable[Element]) -> TextInfo:
    # When no text is found on this page, it means it is full of tables or
    # it is a blank page. We cannot tell if there is a cut in the context.
    start_incision: TextIncision = TextIncision.UNCERTAIN
    end_incision: TextIncision = TextIncision.UNCERTAIN
    tokens: int = 0
    first: Element | None = None
    last: Element | None = None

    for child in children:
      for line in child:
        if line.tag == "line":
          tokens += self._llm.count_tokens_count(line.text)
      if first is None:
        first = child
      last = child

    if first is not None and last is not None:
      if first.tag == "text":
        start_incision = self._attr_value_to_kind(first.attrib.get("start-incision"))
      if last.tag == "text":
        end_incision = self._attr_value_to_kind(last.attrib.get("end-incision"))

    return TextInfo(
      tokens=tokens,
      start_incision=start_incision,
      end_incision=end_incision,
    )

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