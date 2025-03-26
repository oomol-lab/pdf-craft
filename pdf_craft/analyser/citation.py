from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element
from resource_segmentation import split, Resource, Incision

from ..llm import LLM
from .common import PageInfo
from .page_clipper import get_and_clip_pages, PageRef, PageXML
from .asset_matcher import AssetMatcher, ASSET_TAGS
from .chunk_file import ChunkFile
from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .utils import parse_page_indexes


def analyse_citations(
    llm: LLM,
    file: ChunkFile,
    pages: list[PageInfo],
    request_max_tokens: int,
    tail_rate: float,
    report_step: AnalysingStepReport | None,
    report_progress: AnalysingProgressReport | None,
  ):

  prompt_tokens = llm.prompt_tokens_count("citation", {})
  data_max_tokens = request_max_tokens - prompt_tokens
  if data_max_tokens <= 0:
    raise ValueError(f"Request max tokens is too small (less than system prompt tokens count {prompt_tokens})")

  groups = file.filter_groups(split(
    max_segment_count=data_max_tokens,
    gap_rate=tail_rate,
    tail_rate=1.0,
    resources=_extract_citations(pages),
  ))
  if report_step is not None:
    groups = list(groups)
    report_step(AnalysingStep.EXTRACT_CITATION, len(groups))

  for i, (start_idx, end_idx, task_group) in enumerate(groups):
    page_xml_list = get_and_clip_pages(
      llm=llm,
      group=task_group,
      get_element=lambda i: _get_citation_with_file(pages, i),
    )
    raw_pages_root = Element("pages")
    for j, page_xml in enumerate(page_xml_list):
      element = page_xml.xml
      element.set("idx", str(j + 1))
      raw_pages_root.append(element)

    asset_matcher = AssetMatcher().register_raw_xml(raw_pages_root)
    response_xml = llm.request_xml("citation", raw_pages_root)
    chunk_xml = Element("chunk", {
      "start-idx": str(start_idx + 1),
      "end-idx": str(end_idx + 1),
    })
    asset_matcher.add_asset_hashes_for_xml(response_xml)

    for citation in _search_and_filter_and_split_citations(
      response_xml=response_xml,
      page_xml_list=page_xml_list,
    ):
      chunk_xml.append(citation)

    file.atomic_write_chunk(start_idx, end_idx, chunk_xml)

    if report_progress is not None:
      report_progress(i + 1)

def _extract_citations(pages: Iterable[PageInfo]) -> Generator[Resource[PageRef], None, None]:
  citations_matrix: list[list[Resource[PageRef]]] = []
  current_citations: list[Resource[PageRef]] = []

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
    citations[0].start_incision = Incision.IMPOSSIBLE
    citations[-1].end_incision = Incision.IMPOSSIBLE

  for citations in citations_matrix:
    yield from citations

def _get_citation_with_file(pages: list[PageInfo], index: int) -> Element:
  page = next((p for p in pages if p.page_index == index), None)
  assert page is not None
  assert page.citation is not None

  with page.file() as file:
    root: Element = fromstring(file.read())
    citation = root.find("citation")
    for child in citation:
      if child.tag not in ASSET_TAGS:
        child.attrib = {}
    return citation

def _search_and_filter_and_split_citations(response_xml: Element, page_xml_list: list[PageXML]):
  for citation in response_xml:
    page_indexes = parse_page_indexes(citation)
    page_xml = page_xml_list[page_indexes[0] - 1]

    if page_xml.is_gap:
      continue

    attributes = {
      **citation.attrib,
      "idx": ",".join([
        str(page_xml_list[p - 1].page_index + 1)
        for p in page_indexes
      ]),
    }
    # after testing, LLM will merge multiple citations together, which will result in multiple
    # labels for one citation. the code here handles this as a backup.
    splitted_citation: Element | None = None

    for child in citation:
      if child.tag == "label":
        if splitted_citation is not None:
          yield splitted_citation
        splitted_citation = Element("citation", {**attributes})

      if splitted_citation is not None:
        splitted_citation.append(child)

    if splitted_citation is not None:
      yield splitted_citation
