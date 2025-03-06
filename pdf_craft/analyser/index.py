from __future__ import annotations

import json

from io import StringIO
from typing import Generator
from dataclasses import dataclass
from xml.etree.ElementTree import Element
from .llm import LLM
from .utils import encode_response, normalize_xml_text


_SYMBOLS = ("*", "+", "-")
_INTENTS = 2

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
      if child.tag == "headline":
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

  def identify_chapter(self, headline: str, level: int) -> Chapter | None:
    headline = normalize_xml_text(headline)

    for chapter, chapter_stack in self._search_from_stack(self._stack):
      # level of PREFACES & CHAPTERS is 0
      chapter_level = len(chapter_stack) - 1
      if level == chapter_level and chapter.headline == headline:
        self._stack = chapter_stack
        return chapter

    # there is no other solution, the expedient measure is to ignore the level
    for _, chapter in self._iter_chapters(self._root.children):
      if chapter.headline == headline:
        return chapter

    return None

  @property
  def markdown(self) -> str:
    buffer = StringIO()
    prefaces = self._root.children[0].children
    chapters = self._root.children[1].children

    if len(prefaces) == 0:
      self._write_markdown(buffer, chapters)
    else:
      buffer.write("### Prefaces\n\n")
      self._write_markdown(buffer, prefaces)
      buffer.write("\n")
      buffer.write("### Chapters\n\n")
      self._write_markdown(buffer, chapters)
    return buffer.getvalue()

  def _write_markdown(self, buffer: StringIO, chapters: list[Chapter]):
    for deep, chapter in self._iter_chapters(chapters):
      for _ in range(deep * _INTENTS):
        buffer.write(" ")
      buffer.write(_SYMBOLS[deep % len(_SYMBOLS)])
      buffer.write(" ")
      buffer.write(chapter.headline)
      buffer.write("\n")

  def reset_stack_with_chapter(self, chapter: Chapter):
    for found_chapter, stack in self._search_from_stack(None):
      if found_chapter.id == chapter.id:
        self._stack = stack
        return
    self._stack = None

  def _iter_chapters(self, chapters: list[Chapter], deep: int = 0) -> Generator[tuple[int, Chapter], None, None]:
    for chapter in chapters:
      yield deep, chapter
      yield from self._iter_chapters(chapter.children, deep + 1)

  # start traversing the tree in pre-order from the current position of the stack
  def _search_from_stack(self, stack: list[int] | None) -> Generator[tuple[Chapter, list[int]], None, None]:
    if stack is None:
      stack = [0]
    chapter: Chapter = self._root
    chapters_stack: list[tuple[int, Chapter]] = []
    for index in stack:
      chapter = chapter.children[index]
      chapters_stack.append((index, chapter))

    while True:
      _, chapter = chapters_stack[-1]
      if len(chapter.children) > 0:
        chapters_stack.append((0, chapter.children[0]))
      else:
        move_success = self._move_to_next(chapters_stack)
        if not move_success:
          return
      _, chapter = chapters_stack[-1]

      # skip PREFACES & CHAPTERS
      if len(chapters_stack) > 1:
        yield chapter, [i for i, _ in chapters_stack]

  # @return True if has next
  def _move_to_next(self, stack: list[tuple[int, Chapter]]) -> bool:
    while True:
      index, _ = stack.pop()
      index += 1
      if len(stack) > 0:
        _, parent = stack[-1]
        if index >= len(parent.children):
          continue
        stack.append((index, parent.children[index]))
        return True
      elif index < len(self._root.children):
        stack.append((index, self._root.children[index]))
        return True
      else:
        return False