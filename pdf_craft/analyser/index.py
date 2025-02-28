from __future__ import annotations

import re

from io import StringIO
from typing import Generator
from dataclasses import dataclass
from xml.etree.ElementTree import Element


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
    self._stack: list[int] = []

    self._chapters: list[Chapter] = self._parse_chapters(chapters)
    self._prefaces: list[Chapter] = []
    if prefaces is not None:
      self._prefaces = self._parse_chapters(prefaces)

    self._root: Chapter = Chapter(
      id=0,
      headline="ROOT",
      children=[self._prefaces, self._chapters],
    )
    next_id: int = 0
    for _, chapter in self._iter_chapters(self._root):
      chapter.id = next_id
      next_id += 1

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
        headline=re.sub(r"\s+", " ", headline.text).strip(),
        children=self._parse_chapters(children) if children is not None else [],
      ))
    return chapters

  @property
  def markdown(self) -> str:
    buffer = StringIO()
    if len(self._prefaces) == 0:
      self._write_markdown(buffer, self._chapters)
    else:
      buffer.write("### Prefaces\n\n")
      self._write_markdown(buffer, self._prefaces)
      buffer.write("\n")
      buffer.write("### Chapters\n\n")
      self._write_markdown(buffer, self._chapters)
    return buffer.getvalue()

  def _write_markdown(self, buffer: StringIO, chapters: list[Chapter]):
    for deep, chapter in self._iter_chapters(chapters):
      for _ in range(deep * _INTENTS):
        buffer.write(" ")
      buffer.write(_SYMBOLS[deep % len(_SYMBOLS)])
      buffer.write(" ")
      buffer.write(chapter.headline)
      buffer.write("\n")

  @property
  def stack(self) -> list[Chapter]:
    chapter = self._root
    stack: list[Chapter] = []
    for index in self._stack:
      chapter = chapter.children[index]
      stack.append(chapter)
    return stack

  def identify_chapter(self, headline: str, level: int) -> Chapter | None:
    for chapter, chapter_stack in self._search_from_stack(self._stack):
      if level == len(chapter_stack) and chapter.headline == headline:
        self._stack = chapter_stack
        return chapter

    # there is no other solution, the expedient measure is to ignore the level
    for _, chapter in self._iter_chapters(self._root):
      if chapter.headline == headline:
        return chapter

    return None

  def reset_stack_with_chapter(self, chapter: Chapter):
    for found_chapter, stack in self._search_from_stack([]):
      if found_chapter.id == chapter.id:
        self._stack = stack
        return
    raise ValueError("The chapter is not found in the index")

  def _iter_chapters(self, chapters: list[Chapter], deep: int = 0) -> Generator[tuple[int, Chapter], None, None]:
    for chapter in chapters:
      yield deep, chapter
      yield from self._iter_chapters(chapter.children, deep + 1)

  # start traversing the tree in pre-order from the current position of the stack
  def _search_from_stack(self, stack: list[int]) -> Generator[tuple[Chapter, list[int]], None, None]:
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
      yield chapter, [i for i, _ in chapters_stack]

  # @return True if has next
  def _move_to_next(self, stack: list[tuple[int, Chapter]]) -> bool:
    while True:
      index, _ = stack.pop()
      index += 1
      if len(stack) == 0:
        return False

      _, parent = stack[-1]
      if index >= len(parent.children):
        continue

      stack.append((index, parent.children[index]))
      return True