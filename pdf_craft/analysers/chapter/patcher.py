from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterator
from xml.etree.ElementTree import Element

from ..contents import Contents, Chapter
from ..sequence import decode_paragraph
from ..data import Paragraph
from ..utils import xml_files, read_xml_file, XML_Info


def read_paragraphs(paragraph_path: Path) -> Generator[Paragraph, None, None]:
  for file_path, _, page_index, order_index in xml_files(paragraph_path):
    raw_root = read_xml_file(file_path)
    root = Element(raw_root.tag, attrib=raw_root.attrib)
    for layout in raw_root:
      if layout.get("id", None) is None:
        continue
      if _is_invalid_layout_element(layout):
        continue
      root.append(layout)

    yield decode_paragraph(
      element=root,
      page_index=page_index,
      order_index=order_index,
    )

def read_paragraphs_with_patches(
      paragraph_path: Path,
      contents: Contents,
      map_path: Path,
    ) -> Generator[tuple[Chapter | None, Paragraph], None, None]:

  contents_page_indexes = set(contents.page_indexes)
  reader = _PatcherReader(
    map_path=map_path,
    no_matched_chapters=dict((c.id, c) for c in contents),
  )
  for file_path, _, page_index, order_index in xml_files(paragraph_path):
    if page_index in contents_page_indexes:
      continue # skip contents

    raw_root = read_xml_file(file_path)
    root = Element(raw_root.tag, attrib=raw_root.attrib)
    root_chapter: Chapter | None = None

    for raw_layout in raw_root:
      id = raw_layout.get("id", None)
      if id is None:
        continue

      page_index, _ = id.split("/", maxsplit=1)
      page_index = int(page_index)
      patch = reader.read(page_index)

      chapter: Chapter | None = None
      layout: Element = raw_layout
      if patch is not None:
        chapter = patch.headline2chapter.get(id, None)
        layout = patch.layout_xmls_patches.get(id, raw_layout)

      if chapter is not None:
        root_chapter = chapter

      if _is_invalid_layout_element(layout):
        continue # means it was removed

      if root_chapter is not None and len(root) > 0:
        # paragraph maybe splitted by a chapter headline
        yield root_chapter, decode_paragraph(
          element=root,
          page_index=page_index,
          order_index=order_index,
        )
        root = Element(raw_root.tag, attrib=raw_root.attrib)

      root.append(layout)

    if len(root) > 0:
      yield root_chapter, decode_paragraph(
        element=root,
        page_index=page_index,
        order_index=order_index,
      )

def _is_invalid_layout_element(layout: Element) -> bool:
  if len(layout) > 0:
    return False # means it wasn't removed
  if layout.tag in ("figure", "table", "formula"):
    return False
  return True

@dataclass
class _Patch:
  range: tuple[int, int]
  headline2chapter: dict[str, Chapter]
  layout_xmls_patches: dict[str, Element]

class _PatcherReader:
  def __init__(self, map_path: Path, no_matched_chapters: dict[int, Chapter]):
    self._pre_page_index: int = 0
    self._current_patch: _Patch | None = None
    self._xml_infos_iter: Iterator[XML_Info, None, None] = iter(xml_files(map_path))
    self._no_matched_chapters: dict[int, Chapter] = no_matched_chapters

  def read(self, page_index: int) -> _Patch | None:
    assert self._pre_page_index <= page_index
    self._pre_page_index = page_index

    while True:
      if self._current_patch is not None:
        patch_begin, patch_end = self._current_patch.range
        if page_index < patch_begin:
          return None
        if page_index <= patch_end:
          return self._current_patch

      xml_info = next(self._xml_infos_iter, None)
      if xml_info is None:
        self._current_patch = None
        return None

      file_path, _, begin, end = xml_info
      map_element = read_xml_file(file_path)
      self._current_patch = _Patch(
        range=(begin, end),
        headline2chapter={},
        layout_xmls_patches={},
      )
      for child in map_element:
        if child.tag != "mapper":
          continue
        headline_id = child.get("headline-id", None)
        chapter_id = int(child.get("chapter-id", "-1"))
        chapter = self._no_matched_chapters.pop(chapter_id, None)
        if headline_id is None or chapter is None:
          continue
        self._current_patch.headline2chapter[headline_id] = chapter

      patch_element = map_element.find("patch")
      if patch_element is not None:
        for child in patch_element:
          id = child.get("id", None)
          if id is None:
            continue
          self._current_patch.layout_xmls_patches[id] = child
