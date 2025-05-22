import os
import re

from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import fromstring, Element

XML_Info = tuple[Path, str, int, int]


def remove_file(file_path: Path) -> None:
  if file_path.exists():
    os.unlink(file_path)

def read_xml_file(file_path: Path) -> Element:
  with file_path.open("r", encoding="utf-8") as file:
    return fromstring(file.read())

def xml_files(dir_path: Path) -> list[XML_Info]:
  xml_infos: list[XML_Info] = []
  for file in dir_path.iterdir():
    file_path = dir_path / file
    if not file_path.is_file():
      continue
    file_prefix, index1, index2 = _split_index_suffix(file.name)
    if file_prefix is None:
      continue

    xml_infos.append((file_path, file_prefix, index1, index2))

  xml_infos.sort(key=lambda x: (x[2], x[3]))
  return xml_infos

def search_xml_children(parent: Element) -> Generator[tuple[Element, Element], None, None]:
  for child in parent:
    yield child, parent
    yield from search_xml_children(child)

def _split_index_suffix(file_name: str) -> tuple[str | None, int, int]:
  matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
  if not matches:
    return None, -1, -1

  file_prefix: str
  index1: str
  index2: str
  cells = re.sub(r"\..*$", "", file_name).split("_")
  if len(cells) == 3:
    file_prefix, index1, index2 = cells
  else:
    file_prefix, index1 = cells
    index2 = index1

  return file_prefix, int(index1), int(index2)