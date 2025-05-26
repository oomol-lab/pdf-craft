import re
import io
import shutil

from json import dumps
from pathlib import Path
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
    chapter_output_path: Path,
    assets_path: Path,
  ) -> None:

  if contents is not None:
    index_path = output_path / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
      f.write(dumps(contents.json(), ensure_ascii=False, indent=2))

  meta_path = output_path / "meta.json"
  with open(meta_path, "w", encoding="utf-8") as f:
    # TODO: complete metadata extraction logic
    meta = {
      "title": "Test book title",
      "authors": ["Tao Zeyu"],
    }
    f.write(dumps(meta, ensure_ascii=False, indent=2))

  cover_path = assets_path / "cover.png"
  output_chapters_path = output_path / "chapters"
  output_assets_path = output_path / "assets"

  if cover_path.exists():
    shutil.copy(cover_path, output_path / "cover.png")

  asset_hash_set: set[str] = set()
  output_chapters_path.mkdir(parents=True, exist_ok=True)

  for file in chapter_output_path.iterdir():
    if file.is_file() and _CHAPTER_FILE_PATTERN.match(file.name):
      chapter = _handle_chapter(asset_hash_set, file)
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

def _handle_chapter(asset_hash_set: set[str], origin_path: Path) -> Element:
  raw_chapter_element = read_xml_file(origin_path)
  chapter_element = Element(
    raw_chapter_element.tag,
    attrib=raw_chapter_element.attrib,
  )
  for raw_layout in raw_chapter_element:
    layout = Element(raw_layout.tag)
    text_buffer = io.StringIO()
    caption_elements: list[Element] = []
    mark_elements: list[Element] = []

    if raw_layout.tag in ASSET_LAYOUT_KINDS:
      layout_hash = raw_layout.get("hash", None)
      if layout_hash is not None:
        asset_hash_set.add(layout_hash)
        layout.set("hash", layout_hash)
      for child in raw_layout:
        if child.tag == "caption":
          caption_elements.append(_handle_caption(child))
        elif child.text and child.tag == "latex":
          text_buffer.write(child.text.strip())
    else:
      for child in raw_layout:
        if child.tag == "mark":
          mark_elements.append(child)
        elif child.text and child.tag == "line":
          text_buffer.write(child.text.strip())

    layout.text = text_buffer.getvalue()
    if layout.tag == "footnote":
      layout.extend(mark_elements)
    layout.extend(caption_elements)
    chapter_element.append(layout)

  return chapter_element

def _handle_caption(raw_caption: Element) -> Element:
  text_buffer = io.StringIO()
  caption = Element(
    raw_caption.tag,
    attrib=raw_caption.attrib,
  )
  for child in raw_caption:
    if child.text and child.tag == "line":
      text_buffer.write(child.text.strip())
  caption.text = text_buffer.getvalue()
  return caption