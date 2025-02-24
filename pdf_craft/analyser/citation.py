import os

from typing import Iterable, Generator
from xml.etree.ElementTree import tostring, fromstring, Element

from .llm import LLM
from .types import PageInfo, TextInfo
from .segment import allocate_segments
from .group import group
from .page_clipper import get_and_clip_pages


def analyse_citations(
    llm: LLM,
    pages: list[PageInfo],
    pages_dir_path: str,
    request_max_tokens: int,
    tail_rate: float):

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
    page_xml_list, head_count, tail_count = get_and_clip_pages(
      llm=llm,
      group=task_group,
      get_element=lambda i: _get_citation_with_file(pages[i].file_name, pages_dir_path),
    )
    raw_pages_xml = Element("pages")
    for i, page_xml in enumerate(page_xml_list):
      page_xml.set("index", str(i))
      raw_pages_xml.append(page_xml)

    response_xml = llm.request("citation", raw_pages_xml)
    print("\n", tostring(response_xml, encoding="unicode"), "\n")

def _extract_citations(pages: Iterable[PageInfo]) -> Generator[TextInfo, None, None]:
  for page in pages:
    citation = page.citation
    if citation is not None:
      yield citation

def _get_citation_with_file(file_name: str, file_dir_path: str) -> Element:
  file_path = os.path.join(file_dir_path, file_name)
  with open(file_path, "r", encoding="utf-8") as file:
    root: Element = fromstring(file.read())
    return root.find("citation")