import os
import re

from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

def normalize_xml_text(xml_text: str) -> str:
  return re.sub(r"\s+", " ", xml_text).strip()

def read_xml_files(dir_path: str, enable_kinds: Iterable[str]) -> Generator[tuple[Element, str, str, int, int], None, None]:
  for file_name, kind, index1, index2 in read_files(dir_path, enable_kinds):
    file_path = os.path.join(dir_path, file_name)
    with open(file_path, "r", encoding="utf-8") as file:
      root = fromstring(file.read())
      yield root, file_name, kind, index1, index2

def search_xml_and_indexes(kind: str, dir_path: str) -> Generator[tuple[str, int, int], None, None]:
  for file_name in os.listdir(dir_path):
    matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
    if not matches:
      continue
    file_kind: str
    index1: str
    index2: str
    cells = re.sub(r"\..*$", "", file_name).split("_")
    if len(cells) == 3:
      file_kind, index1, index2 = cells
    else:
      file_kind, index1 = cells
      index2 = index1
    if kind != file_kind:
      continue
    yield file_name, int(index1) - 1, int(index2) - 1

def read_files(dir_path: str, enable_kinds: Iterable[str]) -> Generator[tuple[str, str, int, int], None, None]:
  for file_name in os.listdir(dir_path):
    matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
    if not matches:
      continue

    kind: str
    index1: str
    index2: str = "-1"
    cells = re.sub(r"\..*$", "", file_name).split("_")

    if len(cells) == 3:
      kind, index1, index2 = cells
    else:
      kind, index1 = cells

    if kind not in enable_kinds:
      continue

    yield file_name, kind, int(index1), int(index2)

def search_xml_children(parent: Element) -> Generator[tuple[Element, Element], None, None]:
  for child in parent:
    yield child, parent
    yield from search_xml_children(child)

def parse_page_indexes(element: Element) -> list[int]:
  idx = element.get("idx")
  if idx is None:
    return []
  page_indexes = [int(i) - 1 for i in idx.split(",")]
  page_indexes.sort()
  return page_indexes

def group_range(indexes: Iterable[int]) -> Generator[range, None, None]:
  indexes = list(indexes)
  indexes.sort()
  current_range: tuple[int, int] | None = None

  for index in indexes:
    if current_range is None:
      current_range = (index, index)
    else:
      start, end = current_range
      if end + 1 == index:
        current_range = (start, index)
      else:
        yield range(start, end + 1)
        current_range = (index, index)

  if current_range is not None:
    start, end = current_range
    yield range(start, end + 1)