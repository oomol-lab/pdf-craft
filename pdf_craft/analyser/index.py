from __future__ import annotations

import json

from typing import Generator
from dataclasses import dataclass
from xml.etree.ElementTree import Element
from .llm import LLM
from .utils import encode_response, normalize_xml_text


def parse_index(
    start_page_index: int,
    end_page_index: int,
    root: Element,
  ) -> Index | None:

  prefaces: Element | None = None
  chapters: Element | None = None

  for child in root:
    if child.tag == "prefaces":
      prefaces = child
    elif child.tag == "chapters":
      chapters = child

  if chapters is None:
    if prefaces is None:
      return None
    chapters = prefaces

  return Index(
    start_page_index=start_page_index,
    end_page_index=end_page_index,
    chapters=chapters,
    prefaces=prefaces,
  )

@dataclass
class Chapter:
  id: int
  headline: str
  children: list[Chapter]

class Index:
  def __init__(
      self,
      start_page_index: int,
      end_page_index: int,
      chapters: Element,
      prefaces: Element | None,
    ):
    self._start_page_index: int = start_page_index
    self._end_page_index: int = end_page_index
    self._root: Chapter = self._create_root(chapters, prefaces)
    self._stack: list[int] | None = None

  def _create_root(self, chapters: Element, prefaces: Element | None) -> Chapter:
    next_id: int = 0
    chapters: list[Chapter] = self._parse_chapters(chapters)
    prefaces: list[Chapter]
    if prefaces is not None:
      prefaces = self._parse_chapters(prefaces)
    else:
      prefaces = []

    root: Chapter = Chapter(
      id=0,
      headline="ROOT",
      children=[
        Chapter(
          id=0,
          headline="PREFACES",
          children=prefaces,
        ),
        Chapter(
          id=0,
          headline="CHAPTERS",
          children=chapters,
        )
      ],
    )
    for _, chapter in self._iter_chapters(root.children):
      chapter.id = next_id
      next_id += 1

    return root

  def _parse_chapters(self, parent: Element) -> list[Chapter]:
    chapters: list[Chapter] = []
    for chapter in parent:
      if chapter.tag != "chapter":
        continue
      headline = chapter.find("headline")
      children = chapter.find("children")
      if headline is None:
        continue
      chapters.append(Chapter(
        id=0,
        headline=normalize_xml_text(headline.text),
        children=self._parse_chapters(children) if children is not None else [],
      ))
    return chapters

  def mark_ids_for_headlines(self, llm: LLM, content_xml: Element):
    raw_pages_root = Element("pages")
    origin_headlines: list[Element] = []

    for child in content_xml:
      if child.tag != "headline":
        continue
      page_index = int(child.get("idx", "-1"))
      if page_index <= self._end_page_index:
        # the reader has not yet read the catalogue.
        continue
      headline = Element("headline")
      headline.text = normalize_xml_text(child.text)
      raw_pages_root.append(headline)
      origin_headlines.append(child)

    response = llm.request("headline", raw_pages_root, {
      "index": json.dumps(
        obj=self.json,
        ensure_ascii=False,
        indent=2,
      ),
    })
    for i, headline in enumerate(encode_response(response)):
      id = headline.get("id")
      if id is not None and i < len(origin_headlines):
        origin_headline = origin_headlines[i]
        origin_headline.set("id", id)
        origin_headline.text = normalize_xml_text(headline.text)

  @property
  def json(self) -> dict:
    prefaces = self._root.children[0].children
    chapters = self._root.children[1].children

    if len(prefaces) == 0:
      return self._json_list(chapters)
    else:
      return {
        "prefaces": self._json_list(prefaces),
        "chapters": self._json_list(chapters),
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

  def _iter_chapters(self, chapters: list[Chapter], deep: int = 0) -> Generator[tuple[int, Chapter], None, None]:
    for chapter in chapters:
      yield deep, chapter
      yield from self._iter_chapters(chapter.children, deep + 1)