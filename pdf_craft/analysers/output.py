import re
import shutil

from json import dumps
from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import Element

from ..xml import encode
from .contents import Contents
from .data import ASSET_LAYOUT_KINDS
from .utils import read_xml_file


_CHAPTER_FILE_PATTERN = re.compile(r"chapter(_\d+)?\.xml$")
_ASSET_FILE_PATTERN = re.compile(r"([0-9a-f]+)\.[a-zA-Z0-9]+$")

def output(
    contents: Contents | None,
    output_path: Path,
    meta_path: Path,
    chapter_output_path: Path,
    assets_path: Path,
  ) -> None:

  if contents is not None:
    index_path = output_path / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
      f.write(dumps(contents.json(), ensure_ascii=False, indent=2))

  cover_path = assets_path / "cover.png"
  output_chapters_path = output_path / "chapters"
  output_assets_path = output_path / "assets"

  if cover_path.exists():
    shutil.copy(cover_path, output_path / "cover.png")
  shutil.copy(meta_path, output_path / "meta.json")

  asset_hash_set: set[str] = set()
  output_chapters_path.mkdir(parents=True, exist_ok=True)

  for file in chapter_output_path.iterdir():
    if file.is_file() and _CHAPTER_FILE_PATTERN.match(file.name):
      chapter = _transform_chapter(file)
      asset_hash_set.update(_search_asset_hashes(chapter))
      target_path = output_chapters_path / file.name
      with open(target_path, "w", encoding="utf-8") as f:
        f.write(encode(chapter))

  if asset_hash_set:
    output_assets_path.mkdir(parents=True, exist_ok=True)
    for file in assets_path.iterdir():
      if not file.is_file():
        continue
      match = _ASSET_FILE_PATTERN.match(file.name)
      if match is None:
        continue
      asset_hash = match.group(1)
      if asset_hash not in asset_hash_set:
        continue
      shutil.copy(file, output_assets_path / file.name)

def _search_asset_hashes(chapter: Element):
  for chapter_child in chapter:
    if chapter_child.tag == "footnote":
      for footnote_child in chapter_child:
        hash = _get_asset_hash(footnote_child)
        if hash is not None:
          yield hash
    else:
      hash = _get_asset_hash(chapter_child)
      if hash is not None:
        yield hash

def _get_asset_hash(layout: Element) -> str | None:
  if layout.tag in ASSET_LAYOUT_KINDS:
    return layout.get("hash", None)
  else:
    return None

def _transform_chapter(origin_path: Path) -> Element:
  raw_chapter_element = read_xml_file(origin_path)
  chapter_element = Element(
    raw_chapter_element.tag,
    attrib=raw_chapter_element.attrib,
  )
  for child in raw_chapter_element:
    if child.tag == "footnote":
      child = _transform_footnote(child)
    else:
      child = _transform_layout(child)
    chapter_element.append(child)

  return chapter_element

def _transform_footnote(raw_footnote: Element) -> Element:
  footnote = Element(raw_footnote.tag)
  footnote.set("id", raw_footnote.get("id"))
  for child in raw_footnote:
    if child.tag == "mark":
      mark_element = Element(child.tag, attrib=child.attrib)
      footnote.append(mark_element)
      mark_element.text = (child.text or "").strip()
    else:
      layout = _transform_layout(child)
      footnote.append(layout)
  return footnote

def _transform_layout(raw_layout: Element) -> Element:
  layout = Element(raw_layout.tag)

  if raw_layout.tag not in ASSET_LAYOUT_KINDS:
    _fill_lines(layout, raw_layout)
  else:
    layout_hash = raw_layout.get("hash", None)
    if layout_hash is not None:
      layout.set("hash", layout_hash)
    for child in raw_layout:
      if child.tag == "caption":
        caption_element = Element(child.tag)
        layout.append(caption_element)
        _fill_lines(caption_element, child)
      elif child.text and child.tag == "latex":
        layout.text = child.text.strip()

  return layout

def _fill_lines(target_element: Element, line_elements: Iterable[Element]):
  text_buffer: list[str] = []
  last_mark_element: Element | None = None

  def flush_buffer():
    nonlocal text_buffer, last_mark_element
    if len(text_buffer) > 0:
      if last_mark_element is None:
        target_element.text = "".join(text_buffer)
      else:
        last_mark_element.tail = "".join(text_buffer)
      text_buffer.clear()

  for line_element in line_elements:
    if line_element.tag != "line":
      continue
    text = (line_element.text or "").strip()
    if text:
      text_buffer.append(text)
    for child in line_element:
      if child.tag == "mark":
        flush_buffer()
        target_element.append(child)
        last_mark_element = child
      text = (child.tail or "").strip()
      if text:
        text_buffer.append(text)

  flush_buffer()