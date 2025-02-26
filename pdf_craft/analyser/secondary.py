import os
import re

from typing import Iterable
from xml.etree.ElementTree import fromstring, tostring, Element
from .types import PageInfo, TextInfo, TextIncision, IndexInfo
from .llm import LLM
from .citation import analyse_citations


class SecondaryAnalyser:
  def __init__(self, llm: LLM, dir_path: str):
    self._llm: LLM = llm
    self._assets_dir_path = os.path.join(dir_path, "assets")
    self._pages: list[PageInfo] = []
    self._index: IndexInfo | None = None

    for root, file_name, kind, index1, index2 in self._read_xml_files(
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
        self._index = IndexInfo(
          start_page_index=index1 - 1,
          end_page_index=index2 - 1,
          text=text,
          tokens=llm.count_tokens_count(text),
        )

    self._pages.sort(key=lambda p: p.page_index)

  def analyse_citations(self, output_dir_path: str, request_max_tokens: int, tail_rate: float):
    for page_start_index, page_end_index, chunk_xml in analyse_citations(
      llm=self._llm,
      pages=self._pages,
      request_max_tokens=request_max_tokens,
      tail_rate=tail_rate,
    ):
      file_name = f"chunk_{page_start_index + 1}_{page_end_index + 1}.xml"
      file_path = os.path.join(output_dir_path, file_name)

      with open(file_path, "wb") as file:
        file.write(tostring(chunk_xml, encoding="utf-8"))

  def _read_xml_file(self, file_path: str) -> Element:
    with open(file_path, "r", encoding="utf-8") as file:
      return fromstring(file.read())

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

  def _format_index(self, root: Element) -> str:
    return ""

  def _read_xml_files(self, dir_path: str, enable_kinds: Iterable[str]):
    for file_name in os.listdir(dir_path):
      file_path = os.path.join(dir_path, file_name)
      matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
      if not matches:
        continue

      root: Element
      kind: str
      index1: str
      index2: str = "-1"
      cells = re.sub(r"\..*$", "", file_name).split("_")

      if len(cells) == 3:
        kind, index1, index2 = cells
      else:
        kind, index1 = cells

      if kind not in enable_kinds:
        continue

      with open(file_path, "r", encoding="utf-8") as file:
        root = fromstring(file.read())

      yield root, file_name, kind, int(index1), int(index2)
