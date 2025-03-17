from __future__ import annotations
from json import loads
from dataclasses import dataclass
from typing import Callable
from xml.etree.ElementTree import tostring, Element
from .i18n import I18N
from .template import Template


@dataclass
class NavPoint:
  index_id: int
  order: int
  file_name: str

def gen_index(
    template: Template,
    i18n: I18N,
    meta: dict,
    file_path: str,
    has_cover: bool,
    check_chapter_exits: Callable[[int], bool],
  ) -> tuple[str, list[NavPoint]]:

  prefaces, chapters = _parse_index(file_path)
  nav_points: list[Element] = []
  nav_point_generation = _NavPointGeneration(
    check_chapter_exits=check_chapter_exits,
    chapters_count=(
      _count_chapters(prefaces) +
      _count_chapters(chapters)
    ),
  )
  for chapters_list in (prefaces, chapters):
    for chapter in chapters_list:
      element = nav_point_generation.generate(chapter)
      if element is not None:
        nav_points.append(element)

  depth = max(
    _max_depth(prefaces),
    _max_depth(chapters),
  )
  toc_ncx = template.render(
    template="toc.ncx",
    depth=depth,
    i18n=i18n,
    meta=meta,
    has_cover=has_cover,
    nav_points=[tostring(p, encoding="unicode") for p in nav_points],
  )
  return toc_ncx, nav_point_generation.nav_points

def _count_chapters(chapters: list[_Chapter]) -> int:
  count: int = 0
  for chapter in chapters:
    count += 1 + _count_chapters(chapter.children)
  return count

def _max_depth(chapters: list[_Chapter]) -> int:
  max_depth: int = 0
  for chapter in chapters:
    max_depth = max(
      max_depth,
      _max_depth(chapter.children) + 1,
    )
  return max_depth

class _NavPointGeneration:
  def __init__(self, chapters_count: int, check_chapter_exits: Callable[[int], bool]):
    self._nav_points: list[NavPoint] = []
    self._next_order: int = 1
    self._digits = len(str(chapters_count))
    self._check_chapter_exits: Callable[[int], bool] = check_chapter_exits

  @property
  def nav_points(self) -> list[NavPoint]:
    return self._nav_points

  def generate(self, chapter: _Chapter) -> Element | None:
    if not self._check_chapter_exits(chapter.id):
      return None

    part_id = str(chapter.id).zfill(self._digits)
    file_name = f"part{part_id}.xhtml"
    order = self._next_order

    nav_point_xml = Element("navPoint")
    nav_point_xml.set("id", f"np_{chapter.id}")
    nav_point_xml.set("playOrder", str(order))

    label_xml = Element("navLabel")
    label_text_xml = Element("text")
    label_text_xml.text = chapter.headline
    label_xml.append(label_text_xml)

    content_xml = Element("content")
    content_xml.set("src", f"Text/{file_name}")

    nav_point_xml.append(label_xml)
    nav_point_xml.append(content_xml)

    self._next_order += 1
    self._nav_points.append(NavPoint(
      index_id=chapter.id,
      order=order,
      file_name=file_name,
    ))
    for child in chapter.children:
      child_xml = self.generate(child)
      if child_xml is not None:
        nav_point_xml.append(child_xml)

    return nav_point_xml

@dataclass
class _Chapter:
  id: int
  headline: str
  children: list[_Chapter]

def _parse_index(file_path: str) -> tuple[list[_Chapter], list[_Chapter]]:
  data: dict | list
  with open(file_path, "r", encoding="utf-8") as file:
    data = loads(file.read())
  if isinstance(data, list):
    return [], _transform_chapters(data)
  elif isinstance(data, dict):
    return (
      _transform_chapters(data["prefaces"]),
      _transform_chapters(data["chapters"]),
    )

def _transform_chapters(data_list: list) -> list[_Chapter]:
  chapters: list[_Chapter] = []
  for data in data_list:
    chapters.append(_Chapter(
      id=int(data["id"]),
      headline=data["headline"],
      children=_transform_chapters(data["children"]),
    ))
  return chapters