import os
import re
import sys

from typing import Iterable, Generator
from xml.etree.ElementTree import tostring, fromstring, Element

from .llm import LLM
from .types import PageInfo, TextInfo, TextIncision
from .segment import allocate_segments
from .group import group
from .page_clipper import get_and_clip_pages, PageXML
from .asset_matcher import AssetMatcher, ASSET_TAGS


def analyse_citations(
    llm: LLM,
    pages: list[PageInfo],
    pages_dir_path: str,
    request_max_tokens: int,
    tail_rate: float) -> Generator[tuple[int, int, Element], None, None]:

  prompt_name = "citation"
  prompt_tokens = llm.prompt_tokens_count(prompt_name)
  data_max_tokens = request_max_tokens - prompt_tokens
  if data_max_tokens <= 0:
    raise ValueError(f"Request max tokens is too small (less than system prompt tokens count {prompt_tokens})")

  for task_group in group(
    max_tokens=data_max_tokens,
    gap_rate=tail_rate,
    tail_rate=1.0,
    items=allocate_segments(
      text_infos=_extract_citations(pages),
      max_tokens=data_max_tokens,
    ),
  ):
    page_xml_list = get_and_clip_pages(
      llm=llm,
      group=task_group,
      get_element=lambda i: _get_citation_with_file(pages[i].file_name, pages_dir_path),
    )
    raw_pages_root = Element("pages")
    for i, page_xml in enumerate(page_xml_list):
      element = page_xml.xml
      element.set("page-index", str(i + 1))
      raw_pages_root.append(element)

    asset_matcher = AssetMatcher().register_raw_xml(raw_pages_root)
    raw_data = tostring(raw_pages_root, encoding="unicode")
    response = llm.request("citation", raw_data)

    response_xml = _encode_response(response)
    page_start_index, page_end_index = _get_pages_range(page_xml_list)
    chunk_xml = Element("chunk", {
      "page-start-index": str(page_start_index + 1),
      "page-end-index": str(page_end_index + 1),
    })
    for citation in response_xml:
      page_indexes: list[int] = [int(p) for p in citation.get("page-index").split(",")]
      page_indexes.sort()
      page_xml = page_xml_list[page_indexes[0] - 1]
      if not page_xml.is_gap:
        chunk_xml.append(citation)
        citation.set("page-index", ",".join([
          str(page_xml_list[p - 1].page_index + 1)
          for p in page_indexes
        ]))

    asset_matcher.add_asset_hashes_for_xml(chunk_xml)
    yield page_start_index, page_end_index, chunk_xml

def _extract_citations(pages: Iterable[PageInfo]) -> Generator[TextInfo, None, None]:
  citations_matrix: list[list[TextInfo]] = []
  current_citations: list[TextInfo] = []

  for page in pages:
    citation = page.citation
    if citation is not None:
      current_citations.append(citation)
    elif len(current_citations) > 0:
      citations_matrix.append(current_citations)
      current_citations = []

  if len(current_citations) > 0:
    citations_matrix.append(current_citations)

  for citations in citations_matrix:
    citations[0].start_incision = TextIncision.IMPOSSIBLE
    citations[-1].end_incision = TextIncision.IMPOSSIBLE

  for citations in citations_matrix:
    yield from citations

def _get_citation_with_file(file_name: str, file_dir_path: str) -> Element:
  file_path = os.path.join(file_dir_path, file_name)
  with open(file_path, "r", encoding="utf-8") as file:
    root: Element = fromstring(file.read())
    citation = root.find("citation")
    for child in citation:
      if child.tag not in ASSET_TAGS:
        child.attrib = {}
    return citation

def _encode_response(response: str) -> Element:
  matches = re.findall(r"<response>.*</response>", response, re.DOTALL)
  if not matches or len(matches) == 0:
    raise ValueError("No page tag found in LLM response")
  content: str = matches[0]
  content = content.replace("&", "&amp;")
  try:
    return fromstring(content)
  except Exception as e:
    print(response)
    raise e

def _get_pages_range(page_xml_list: list[PageXML]):
  assert len(page_xml_list) > 0
  page_start_index: int = sys.maxsize
  page_end_index: int = 0
  for page_xml in page_xml_list:
    page_start_index = min(page_start_index, page_xml.page_index)
    page_end_index = max(page_end_index, page_xml.page_index)
  return page_start_index, page_end_index