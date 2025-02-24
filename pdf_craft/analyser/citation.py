import os

from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

from .llm import LLM
from .types import PageInfo, TextInfo
from .segment import allocate_segments
from .group import group
from .page_clipper import get_and_clip_pages


def analyse_citations(
    llm: LLM,
    pages: Iterable[PageInfo],
    request_max_tokens: int,
    tail_rate: float):

  prompt_name = "citation"
  prompt_tokens = llm.prompt_tokens_count(prompt_name)
  data_max_tokens = request_max_tokens - prompt_name
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
    pass

def _extract_citations(pages: Iterable[PageInfo]) -> Generator[TextInfo]:
  for page in pages:
    citation = page.citation
    if citation is not None:
      yield citation
