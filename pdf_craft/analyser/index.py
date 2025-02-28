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

    self._chapters: list[Chapter] = self._parse_chapters(chapters)
    self._prefaces: list[Chapter] = []
    if prefaces is not None:
      self._prefaces = self._parse_chapters(prefaces)

    next_id: int = 0
    for chapters in (self._prefaces, self._chapters):
      for _, chapter in self._iter_chapters(chapters):
        chapter.id = next_id
        next_id += 1

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
    for level, chapter in self._iter_chapters(chapters):
      for _ in range(level * _INTENTS):
        buffer.write(" ")
      buffer.write(_SYMBOLS[level % len(_SYMBOLS)])
      buffer.write(" ")
      buffer.write(chapter.headline)
      buffer.write("\n")

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

  def _iter_chapters(self, chapters: list[Chapter], level: int = 0) -> Generator[tuple[int, Chapter], None, None]:
    for chapter in chapters:
      yield level, chapter
      yield from self._iter_chapters(chapter.children, level + 1)
