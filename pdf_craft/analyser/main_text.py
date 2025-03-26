import os

from typing import Iterable
from xml.etree.ElementTree import fromstring, Element
from resource_segmentation import split, Resource, Incision

from ..llm import LLM
from .common import PageInfo, PageRef
from .chunk_file import ChunkFile
from .index import Index
from .page_clipper import get_and_clip_pages
from .asset_matcher import AssetMatcher
from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .utils import search_xml_and_indexes, parse_page_indexes


def analyse_main_texts(
    llm: LLM,
    file: ChunkFile,
    index: Index | None,
    pages: list[PageInfo],
    citations_dir_path: str,
    request_max_tokens: int, # TODO: not includes tokens of citations
    gap_rate: float,
    report_step: AnalysingStepReport | None,
    report_progress: AnalysingProgressReport | None,
  ):

  prompt_tokens = llm.prompt_tokens_count("main_text", {})
  data_max_tokens = request_max_tokens - prompt_tokens
  citations = _CitationLoader(citations_dir_path)

  if data_max_tokens <= 0:
    raise ValueError(f"Request max tokens is too small (less than system prompt tokens count {prompt_tokens})")

  groups = file.filter_groups(split(
    max_segment_count=data_max_tokens,
    gap_rate=gap_rate,
    tail_rate=0.5,
    resources=_extract_page_text_infos(pages),
  ))
  if report_step is not None:
    groups = list(groups)
    report_step(AnalysingStep.EXTRACT_MAIN_TEXT, len(groups))

  # TODO: 构建 summary class 用 spacy 循环删掉第一句话，以强行将它的范围限定在特定 tokens 数之内。
  #       LLM 的总结有时候会出问题，多次循环后 summary 就不变了。
  summary: str | None = None

  for i, (start_idx, end_idx, task_group)in enumerate(groups):
    page_xml_list = get_and_clip_pages(
      llm=llm,
      group=task_group,
      get_element=lambda i: _get_page_with_file(pages, i),
    )
    if index is not None:
      page_xml_list = [
        p for p in page_xml_list
        if not index.is_index_page_index(p.page_index)
      ]
      if len(page_xml_list) == 0:
        continue

    raw_pages_root = Element("pages")

    for j, page_xml in enumerate(page_xml_list):
      element = page_xml.xml
      element.set("idx", str(j + 1))
      citation = citations.load(page_xml.page_index)
      if citation is not None:
        element.append(citation)
      raw_pages_root.append(element)

    if summary is not None:
      summary_xml = Element("summary")
      summary_xml.text = summary
      raw_pages_root.append(summary_xml)

    asset_matcher = AssetMatcher().register_raw_xml(raw_pages_root)
    response_xml = llm.request_xml("main_text", raw_pages_root)
    chunk_xml = Element("chunk", {
      "start-idx": str(start_idx + 1),
      "end-idx": str(end_idx + 1),
    })
    asset_matcher.add_asset_hashes_for_xml(response_xml)
    abstract_xml = response_xml.find("abstract")
    assert abstract_xml is not None
    content_xml = Element("content")
    chunk_xml.append(abstract_xml)
    chunk_xml.append(content_xml)
    summary = abstract_xml.text

    for child in response_xml.find("content"):
      page_indexes = [
        j for j in parse_page_indexes(child)
        if 0 <= j < len(page_xml_list)
      ]
      if any(not page_xml_list[k].is_gap for k in page_indexes):
        attr_ids: list[str] = []
        for k in page_indexes:
          page_index = page_xml_list[k].page_index + 1
          attr_ids.append(str(page_index))
        child.set("idx", ",".join(attr_ids))
        content_xml.append(child)

    citation_xml = _collect_citations_and_reallocate_ids(raw_pages_root, chunk_xml)
    if citation_xml is not None:
      chunk_xml.append(citation_xml)

    file.atomic_write_chunk(start_idx, end_idx, chunk_xml)

    if report_progress is not None:
      report_progress(i + 1)

def _extract_page_text_infos(pages: list[PageInfo]) -> Iterable[Resource[PageRef]]:
  if len(pages) == 0:
    return

  previous: PageInfo | None = None

  for page in pages:
    if previous is not None and previous.page_index + 1 != page.page_index:
      previous.main.end_incision = Incision.IMPOSSIBLE
      previous = None
    if previous is None:
      page.main.start_incision = Incision.IMPOSSIBLE
    previous = page

  if previous is not None:
    previous.main.end_incision = Incision.IMPOSSIBLE

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

def _collect_citations_and_reallocate_ids(raw_xml: Element, chunk_xml: Element) -> Element | None:
  next_id: int = 1
  citations_map: dict[str, Element] = {}
  ids_map: dict[str, int] = {}
  keep_citations: list[Element] = []

  for page in raw_xml:
    if page.tag != "page":
      continue
    citations = page.find("citations")
    if citations is None:
      continue
    for citation in citations:
      id: str = citation.get("id")
      new_citation = Element("citation")
      citations_map[id] = new_citation
      for child in citation:
        new_citation.append(child)

  for ref in _search_refs(chunk_xml.find("content")):
    origin_id = ref.get("id")
    citation = citations_map.get(origin_id, None)
    if citation is None:
      continue

    id: str | None = ids_map.get(origin_id, None)
    if id is None:
      id = next_id
      next_id += 1
      ids_map[origin_id] = id

    citation.attrib = { "id": str(id) }
    ref.set("id", str(id))
    if citation not in keep_citations:
      keep_citations.append(citation)

  keep_citations.sort(key=lambda c: int(c.get("id")))
  if len(keep_citations) == 0:
    return None

  citations_xml = Element("citations")
  for citation in keep_citations:
    citations_xml.append(citation)
  return citations_xml

def _search_refs(parent: Element):
  for child in parent:
    if child.tag == "ref":
      yield child
    else:
      yield from _search_refs(child)

class _CitationLoader:
  def __init__(self, dir_path: str):
    self._next_citation_id: int = 1
    self._dir_path: str = dir_path
    self._index2file: dict[int, str] = {}

    for file_name, _, _ in search_xml_and_indexes("chunk", dir_path):
      file_path = os.path.join(dir_path, file_name)
      with open(file_path, "r", encoding="utf-8") as file:
        root = fromstring(file.read())
        for page_index in self._read_page_indexes(root):
          self._index2file[page_index] = file_name

  def _read_page_indexes(self, root: Element):
    for child in root:
      if child.tag == "citation":
        page_indexes = parse_page_indexes(child)
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
      page_indexes = parse_page_indexes(child)
      if len(page_indexes) == 0:
        continue
      if page_index != page_indexes[0]:
        continue
      child.attrib = { "id": str(self._next_citation_id) }
      citations.append(child)
      self._next_citation_id += 1

    return citations
