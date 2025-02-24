import os
import re

from typing import Iterable
from natsort import natsorted
from xml.etree.ElementTree import fromstring, tostring, Element
from .types import PageInfo, TextInfo, TextIncision
from .llm import LLM
from .citation import analyse_citations


class SecondaryAnalyser:
  def __init__(self, llm: LLM, dir_path: str):
    self._llm: LLM = llm
    self._assets_dir_path = os.path.join(dir_path, "assets")
    self._pages: list[PageInfo] = []
    self._pages_dir_path: str = os.path.join(dir_path, "pages")
    file_names = natsorted(os.listdir(self._pages_dir_path))
    file_names = [f for f in file_names if re.match(r"^page_\d+\.xml$", f)]

    for page_index, file_name in enumerate(file_names):
      file_path = os.path.join(self._pages_dir_path, file_name)
      with open(file_path, "r", encoding="utf-8") as file:
        root: Element = fromstring(file.read())
        page = self._parse_xml(file_name, page_index, root)
        self._pages.append(page)

  def analyse_citations(self, output_dir_path: str, request_max_tokens: int, tail_rate: float):
    for page_start_index, page_end_index, chunk_xml in analyse_citations(
      llm=self._llm,
      pages=self._pages,
      pages_dir_path=self._pages_dir_path,
      request_max_tokens=request_max_tokens,
      tail_rate=tail_rate,
    ):
      file_name = f"chunk_{page_start_index}_{page_end_index}.xml"
      file_path = os.path.join(output_dir_path, file_name)

      with open(file_path, "wb") as file:
        file.write(tostring(chunk_xml, encoding="utf-8"))

  def _parse_xml(self, file_name: str, page_index: int, root: Element) -> PageInfo:
    main_children: list[Element] = []
    citation: TextInfo | None = None

    for child in root:
      if child.tag == "citation":
        citation = self._parse_text_info(page_index, child)
      else:
        main_children.append(child)

    return PageInfo(
      file_name=file_name,
      page_index=page_index,
      citation=citation,
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