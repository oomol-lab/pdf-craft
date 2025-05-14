from dataclasses import dataclass
from typing import Generator
from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..context import Context
from ..sequence import read_paragraphs, Paragraph, ParagraphType
from ..utils import search_xml_children
from .common import Phase, State


@dataclass
class _Page:
  page_type: ParagraphType
  page_index: int
  paragraphs: list[Paragraph]

def collect(llm: LLM, context: Context[State], sequence_path: Path) -> Generator[Paragraph, None, None]:
  phase = Phase(context.state["phase"])
  page_indexes = context.state["page_indexes"]

  if phase == Phase.INIT:
    page_indexes = []
    for page in _read_pages(sequence_path):
      if page.page_type == ParagraphType.CONTENTS:
        page_indexes.append(page.page_index)
      else:
        break
    phase = Phase.COLLECT
    context.state = {
      **context.state,
      "phase": phase.value,
      "page_indexes": page_indexes,
    }

  if phase == Phase.COLLECT:
    page_indexes.sort()
    for page in _read_pages(sequence_path):
      if page.page_type == ParagraphType.CONTENTS:
        yield from page.paragraphs
        page_indexes.append(page.page_index)
      else:
        if page.page_index in page_indexes:
          yield from page.paragraphs
        elif _check_page_matched(llm, page):
          yield from page.paragraphs
          page_indexes.append(page.page_index)
          context.state = {
            **context.state,
            "page_indexes": page_indexes,
          }
        else:
          break

    phase = Phase.ANALYSE
    context.state = {
      **context.state,
      "phase": phase.value,
      "page_indexes": page_indexes,
    }

  else:
    max_page_index = -1
    if page_indexes:
      max_page_index = max(page_indexes)
    for page in _read_pages(sequence_path):
      if page.page_index in page_indexes:
        yield from page.paragraphs
      elif page.page_index > max_page_index:
        break

def _read_pages(sequence_path: Path) -> Generator[_Page, None, None]:
  page: _Page | None = None
  skip_not_matched = True

  for paragraph in read_paragraphs(sequence_path):
    if paragraph.type == ParagraphType.CONTENTS:
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

def _check_page_matched(llm: LLM, page: _Page) -> bool:
  if page.page_type == ParagraphType.CONTENTS:
    return True

  layouts: list[Element] = []
  for paragraph in page.paragraphs:
    merged_layout: Element | None = None
    for layout in paragraph.layouts:
      if merged_layout is None:
        merged_layout = layout.xml()
      else:
        for line in layout.lines:
          merged_layout.append(line.xml())
    if merged_layout is not None:
      merged_layout.attrib = {}
      layouts.append(merged_layout)

  if len(layouts) == 0:
    return True # maybe it's an empty page or a page fill with a picture

  for layout in layouts:
    for child, _ in search_xml_children(layout):
      if child.tag == "line":
        child.attrib = {}

  request_xml = Element("request")
  request_xml.set("page-index", str(page.page_index))
  request_xml.extend(layouts)
  resp_xml = llm.request_xml(
    template_name="identify_contents",
    user_data=request_xml,
  )
  print(resp_xml)
  raise NotImplementedError("This function is not implemented yet.")