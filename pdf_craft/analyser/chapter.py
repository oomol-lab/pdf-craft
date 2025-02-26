import os

from typing import Any, Iterable, Generator
from xml.etree.ElementTree import tostring, fromstring, Element
from .llm import LLM
from .types import PageInfo, TextInfo, TextIncision, IndexInfo
from .splitter import group, get_pages_range, allocate_segments, get_and_clip_pages
from .asset_matcher import AssetMatcher
from .utils import read_xml_files, encode_response

def analyse_chapters(
    llm: LLM,
    pages: list[PageInfo],
    index: IndexInfo | None,
    citations_dir_path: str,
    request_max_tokens: int, # TODO: not includes tokens of citations
    gap_rate: float,
  ) -> Generator[tuple[int, int, Element], None, None]:

  llm_params: dict[str, Any] = {
    "index": index.text if index is not None else "",
  }
  prompt_tokens = llm.prompt_tokens_count("chapter", llm_params)
  data_max_tokens = request_max_tokens - prompt_tokens
  citations = _CitationLoader(citations_dir_path)

  if data_max_tokens <= 0:
    raise ValueError(f"Request max tokens is too small (less than system prompt tokens count {prompt_tokens})")

  for task_group in group(
    max_tokens=data_max_tokens,
    gap_rate=gap_rate,
    tail_rate=0.5,
    items=allocate_segments(
      text_infos=_extract_page_text_infos(pages),
      max_tokens=data_max_tokens,
    ),
  ):
    page_xml_list = get_and_clip_pages(
      llm=llm,
      group=task_group,
      get_element=lambda i: _get_page_with_file(pages, i),
    )
    raw_pages_root = Element("pages")

    for i, page_xml in enumerate(page_xml_list):
      element = page_xml.xml
      element.set("idx", str(i + 1))
      citation = citations.load(page_xml.page_index)
      if citation is not None:
        element.append(citation)
      raw_pages_root.append(element)

    asset_matcher = AssetMatcher().register_raw_xml(raw_pages_root)
    raw_data = tostring(raw_pages_root, encoding="unicode")
    response = llm.request("chapter", raw_data, llm_params)

    response_xml = encode_response(response)
    page_start_index, page_end_index = get_pages_range(page_xml_list)
    chunk_xml = Element("chunk", {
      "page-start-index": str(page_start_index + 1),
      "page-end-index": str(page_end_index + 1),
    })
    asset_matcher.add_asset_hashes_for_xml(response_xml)
    index_offset = page_xml_list[0].page_index
    abstract_xml = response_xml.find("abstract")
    assert abstract_xml is not None
    content_xml = Element("content")
    chunk_xml.append(abstract_xml)
    chunk_xml.append(content_xml)

    for child in response_xml.find("content"):
      page_indexes = [
        i + index_offset
        for i in _parse_page_indexes(child)
        if 0 <= i + index_offset < len(page_xml_list)
      ]
      if any(i for i in page_indexes if not page_xml_list[i].is_gap):
        child.set("idx", ",".join(str(i + 1) for i in page_indexes))
        content_xml.append(child)

    yield page_start_index, page_end_index, chunk_xml

def _parse_page_indexes(citation: Element) -> list[int]:
  content = citation.get("idx")
  if content is None:
    return []
  else:
    return [int(i) - 1 for i in content.split(",")]

def _extract_page_text_infos(pages: list[PageInfo]) -> Iterable[TextInfo]:
  if len(pages) == 0:
    return

  previous: PageInfo | None = None

  for page in pages:
    if previous is not None and previous.page_index + 1 != page.page_index:
      previous.main.end_incision = TextIncision.IMPOSSIBLE
      previous = None
    if previous is None:
      page.main.start_incision = TextIncision.IMPOSSIBLE
    previous = page

  if previous is not None:
    previous.main.end_incision = TextIncision.IMPOSSIBLE

  for page in pages:
    yield page.main

def _get_page_with_file(pages: list[PageInfo], index: int) -> Element:
  page = next((p for p in pages if p.page_index == index), None)
  assert page is not None

  with page.file() as file:
    root: Element = fromstring(file.read())
    citation = root.find("citation")
    if citation is not None:
      root.remove(citation)
    return root

class _CitationLoader:
  def __init__(self, dir_path: str):
    self._dir_path: str = dir_path
    self._index2file: dict[int, str] = {}
    for root, file_name, _, _, _ in read_xml_files(dir_path, ("chunk",)):
      for page_index in self._read_page_indexes(root):
        self._index2file[page_index] = file_name

  def _read_page_indexes(self, root: Element):
    for child in root:
      if child.tag == "citation":
        page_indexes = _parse_page_indexes(child)
        if len(page_indexes) > 0:
          yield page_indexes[0]

  def load(self, page_index: int) -> Element | None:
    file_name = self._index2file.get(page_index, None)
    if file_name is None:
      return None

    root: Element
    file_path = os.path.join(self._dir_path, file_name)
    with open(file_path, "r", encoding="utf-8") as file:
      root = fromstring(file.read())

    citations = Element("citations")
    for child in root:
      if child.tag != "citation":
        continue
      page_indexes = _parse_page_indexes(child)
      if len(page_indexes) == 0:
        continue
      if page_index != page_indexes[0]:
        continue
      child.attrib = {}
      citations.append(child)

    return citations
