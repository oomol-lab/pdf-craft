from __future__ import annotations
from typing import Iterable, Callable
from dataclasses import dataclass
from xml.etree.ElementTree import Element
from ..llm import LLM
from .utils import group_range, normalize_xml_text


def analyse_index(llm: LLM, raw: Iterable[tuple[int, Element]]) -> dict | None:
  raw_index_pages: list[tuple[int, Element]] = []
  for i, raw_page_xml in raw:
    if raw_page_xml.tag == "index":
      raw_index_pages.append((i, raw_page_xml))

  if len(raw_index_pages) == 0:
    return None, None

  raw_page_xml = Element("index")
  for i, (_, raw_index_xml) in enumerate(raw_index_pages):
    raw_index_xml.set("page-index", str(i + 1))
    raw_page_xml.append(raw_index_xml)

  response_xml = llm.request_xml("index", raw_page_xml)
  index_json = _transform_llm_response_to_json(response_xml)
  ranges = group_range(i + 1 for i, _ in raw_index_pages)
  index_json["ranges"] = [[r[0], r[-1]] for r in ranges]

  return index_json, Index(index_json)

def _transform_llm_response_to_json(response_xml: Element) -> dict:
  prefaces: Element | None = None
  chapters: Element | None = None

  for child in response_xml:
    if child.tag == "prefaces":
      prefaces = child
    elif child.tag == "chapters":
      chapters = child

  if chapters is None:
    if prefaces is None:
      return None
    chapters = prefaces

  previous_id: int = 0
  def gen_id() -> int:
    nonlocal previous_id
    previous_id += 1
    return previous_id

  return {
    "prefaces": _parse_chapters(prefaces, gen_id),
    "chapters": _parse_chapters(chapters, gen_id),
  }

def _parse_chapters(parent: Iterable[Element] | None, gen_id: Callable[[], int]) -> list[Chapter]:
  if parent is None:
    return []

  chapters: list[dict] = []
  for chapter in parent:
    if chapter.tag != "chapter":
      continue

    headline = chapter.find("headline")
    children = chapter.find("children")
    if headline is None:
      continue

    id = gen_id()
    chapters.append({
      "id": str(id),
      "headline": normalize_xml_text(headline.text),
      "children": _parse_chapters(children, gen_id) if children is not None else [],
    })
  return chapters

@dataclass
class Chapter:
  id: int
  headline: str
  children: list[Chapter]

class Index:
  def __init__(self, json_data: dict):
    self._prefaces: list[Chapter] = [self._parse_chapter(c) for c in json_data["prefaces"]]
    self._chapters: list[Chapter] = [self._parse_chapter(c) for c in json_data["chapters"]]
    self._ranges: list[range] = [range(r[0], r[1] + 1) for r in json_data["ranges"]]
    assert len(self._ranges) > 0

  def _parse_chapter(self, data: dict) -> Chapter:
    return Chapter(
      id=int(data["id"]),
      headline=data["headline"],
      children=[self._parse_chapter(child) for child in data["children"]],
    )

  def is_index_page_index(self, page_index: int) -> bool:
    return any(page_index in range for range in self._ranges)

  def after_first_index_page(self, page_index: int) -> bool:
    return page_index > self._ranges[0][-1]

  @property
  def json(self) -> dict:
    if len(self._prefaces) == 0:
      return self._json_list(self._chapters)
    else:
      return {
        "prefaces": self._json_list(self._prefaces),
        "chapters": self._json_list(self._chapters),
      }

  def _json_list(self, chapters: list[Chapter]) -> list[dict]:
    return [
      {
        "id": str(chapter.id),
        "headline": chapter.headline,
        "children": self._json_list(chapter.children),
      }
      for chapter in chapters
    ]
