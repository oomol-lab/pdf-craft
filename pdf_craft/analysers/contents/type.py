from typing import Any, Generator
from dataclasses import dataclass


@dataclass
class Contents:
  page_indexes: list[int]
  prefaces: list["Chapter"]
  chapters: list["Chapter"]

  def __iter__(self) -> Generator["Chapter", None, None]:
    for chapter in self.prefaces:
      yield chapter
      yield from chapter
    for chapter in self.chapters:
      yield chapter
      yield from chapter

  def json(self) -> dict[str, Any]:
    return {
      "prefaces": [chapter.json() for chapter in self.prefaces],
      "chapters": [chapter.json() for chapter in self.chapters],
    }

@dataclass
class Chapter:
  id: int
  name: str
  children: list["Chapter"]

  def __iter__(self) -> Generator["Chapter", None, None]:
    for child in self.children:
      yield child
      yield from child

  def json(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "headline": self.name,
      "children": [child.json() for child in self.children],
    }