from dataclasses import dataclass
from typing import Generator
from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode
from ..utils import Context
from ..sequence import read_paragraphs
from ..data import Paragraph, ParagraphType
from .common import Phase, State
from .utils import normalize_layout_xml


def collect(llm: LLM, context: Context[State], sequence_path: Path) -> Generator[Paragraph, None, None]:
  yield from _Collector(llm, context, sequence_path).do()

@dataclass
class _Page:
  page_type: ParagraphType
  page_index: int
  paragraphs: list[Paragraph]

  def xml(self) -> Element | None:
    page = Element("page")
    page.set("page-index", str(self.page_index))

    for paragraph in self.paragraphs:
      merged_layout = normalize_layout_xml(paragraph)
      if merged_layout is not None:
        page.append(merged_layout)

    return page

class _Collector:
  def __init__(self, llm: LLM, context: Context[State], sequence_path: Path):
    self._llm: LLM = llm
    self._context: Context[State] = context
    self._sequence_path: Path = sequence_path

  def do(self) -> Generator[Paragraph, None, None]:
    phase = Phase(self._context.state["phase"])
    page_indexes = self._context.state["page_indexes"]

    if phase == Phase.INIT:
      page_indexes = []
      for page in self._read_pages():
        if page.page_type == ParagraphType.CONTENTS:
          page_indexes.append(page.page_index)
        else:
          break
      phase = Phase.COLLECT
      self._context.state = {
        **self._context.state,
        "phase": phase.value,
        "page_indexes": page_indexes,
      }
    if not page_indexes:
      return

    if phase == Phase.COLLECT:
      for page in self._read_and_identify_pages(page_indexes):
        if page.page_index not in page_indexes:
          page_indexes.append(page.page_index)
          self._context.state = {
            **self._context.state,
            "page_indexes": page_indexes,
          }
        yield from page.paragraphs

      phase = Phase.ANALYSE
      self._context.state = {
        **self._context.state,
        "phase": phase.value,
      }
    else:
      for page, matched in self._read_pages_with_matched(page_indexes):
        if not matched:
          break
        yield from page.paragraphs

  def _read_and_identify_pages(self, page_indexes: list[int]) -> Generator[_Page, None, None]:
    yield_pages_count: int = 0
    unmatched_pages: list[_Page] = []
    unmatched_elements: list[Element] = []
    unmatched_tokens: int = 0

    def identify_matched_pages_and_check_done() -> tuple[list[_Page], bool]:
      nonlocal yield_pages_count, unmatched_pages, unmatched_elements, unmatched_tokens
      if not unmatched_elements:
        return [], False

      matched_pages: list[_Page] = []
      matched_result = self._check_pages_matched(
        yield_pages_count=yield_pages_count,
        page_elements=unmatched_elements,
      )
      for page, matched in zip(unmatched_pages, matched_result):
        if not matched:
          return matched_pages, True
        matched_pages.append(page)

      unmatched_pages.clear()
      unmatched_elements.clear()
      unmatched_tokens = 0
      return matched_pages, False

    max_data_tokens = self._context.state["max_data_tokens"]

    for page, matched in self._read_pages_with_matched(page_indexes):
      if matched or page.page_type == ParagraphType.CONTENTS:
        if unmatched_pages:
          matched_pages, done = identify_matched_pages_and_check_done()
          yield from matched_pages
          if done:
            return
          yield_pages_count += len(matched_pages)
        yield page
        yield_pages_count += 1
      else:
        page_element = page.xml()
        page_tokens = len(self._llm.encode_tokens(encode(page_element)))
        if unmatched_pages and unmatched_tokens + page_tokens > max_data_tokens:
          matched_pages, done = identify_matched_pages_and_check_done()
          yield from matched_pages
          if done:
            return
          yield_pages_count += len(matched_pages)

        unmatched_pages.append(page)
        unmatched_elements.append(page_element)
        unmatched_tokens += page_tokens

    if unmatched_pages:
      matched_pages, _ = identify_matched_pages_and_check_done()
      yield from matched_pages

  def _read_pages_with_matched(self, page_indexes: list[int]) -> Generator[tuple[_Page, bool], None, None]:
    page_indexes.sort()
    max_page_index = -1
    if page_indexes:
      max_page_index = max(page_indexes)
    for page in self._read_pages():
      if page.page_index in page_indexes:
        yield page, True
      elif page.page_index > max_page_index:
        yield page, False

  def _read_pages(self) -> Generator[_Page, None, None]:
    page: _Page | None = None
    skip_not_matched = True

    for paragraph in read_paragraphs(self._sequence_path):
      if len(paragraph.layouts) == 0:
        continue
      elif paragraph.type == ParagraphType.CONTENTS:
        skip_not_matched = False
      elif skip_not_matched:
        continue

      if page is not None:
        if page.page_index == paragraph.page_index:
          page.paragraphs.append(paragraph)
        else:
          yield page
          page = None

      if page is None:
        page = _Page(
          page_type=paragraph.type,
          page_index=paragraph.page_index,
          paragraphs=[paragraph],
        )
    if page is not None:
      yield page

  def _check_pages_matched(self, yield_pages_count: int, page_elements: list[Element]) -> list[bool]:
    request_xml = Element("request")
    request_xml.extend(page_elements)
    resp_xml = self._llm.request_xml(
      template_name="contents/identifier",
      user_data=request_xml,
      params={
        "last_pages_count": yield_pages_count,
        "current_pages_count": len(page_elements),
      },
    )
    matched_page_indexes: set[int] = set()
    matched: list[bool] = []

    for resp_page in resp_xml:
      page_index = resp_page.get("page-index", None)
      page_type = resp_page.get("type", None)
      if page_index is not None and ParagraphType.CONTENTS == page_type:
        matched_page_indexes.add(page_index)

    for raw_page in page_elements:
      page_index = raw_page.get("page-index", None)
      matched.append(page_index in matched_page_indexes)

    return matched
