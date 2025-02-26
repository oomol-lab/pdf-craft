import os

from typing import Iterable, Generator
from xml.etree.ElementTree import tostring, fromstring, Element
from .llm import LLM
from .types import PageInfo, TextInfo, TextIncision, IndexInfo
from .splitter import group, allocate_segments, get_and_clip_pages
from .asset_matcher import AssetMatcher
from .utils import read_xml_files

def analyse_chapters(
    llm: LLM,
    pages: list[PageInfo],
    index: IndexInfo | None,
    citations_dir_path: str,
    request_max_tokens: int, # TODO: not includes tokens of citations
    gap_rate: float,
  ) -> Generator[tuple[int, int, Element], None, None]:

  prompt_tokens = llm.prompt_tokens_count(
    "chapter", {"index": index.text},
  )
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
      element.set("page-index", str(i + 1))
      citation = citations.load(page_xml.page_index)
      if citation is not None:
        element.append(citation)
      raw_pages_root.append(element)

     # pylint: disable=unused-variable
    asset_matcher = AssetMatcher().register_raw_xml(raw_pages_root)
    raw_data = tostring(raw_pages_root, encoding="unicode")
    response = llm.request("chapter", raw_data, {"index": index.text})
    print(response)

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
  assert page.citation is not None

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
        page_indexes = self._parse_page_indexes(child)
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
      page_indexes = self._parse_page_indexes(child)
      if len(page_indexes) == 0:
        continue
      if page_index != page_indexes[0]:
        continue
      child.attrib = {}
      citations.append(child)

    return citations

  def _parse_page_indexes(self, citation: Element) -> list[int]:
    content = citation.get("page-index")
    if content is None:
      return []
    else:
      return [int(i) - 1 for i in content.split(",")]