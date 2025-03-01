import os
import re

from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

def read_xml_files(dir_path: str, enable_kinds: Iterable[str]) -> Generator[tuple[Element, str, str, int, int], None, None]:
  for file_name, kind, index1, index2 in read_files(dir_path, enable_kinds):
    file_path = os.path.join(dir_path, file_name)
    with open(file_path, "r", encoding="utf-8") as file:
      root = fromstring(file.read())
      yield root, file_name, kind, index1, index2

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

def search_xml_children(parent: Element) -> Generator[Element, None, None]:
  for child in parent:
    yield child
    yield from search_xml_children(child)

def parse_page_indexes(element: Element) -> list[int]:
  idx = element.get("idx")
  if idx is None:
    return []
  else:
    return [int(i) - 1 for i in idx.split(",")]

def encode_response(response: str) -> Element:
  matches = re.findall(r"<response>.*</response>", response, re.DOTALL)
  if not matches or len(matches) == 0:
    raise ValueError("No page tag found in LLM response")
  content: str = matches[0]
  content = content.replace("&", "&amp;")
  try:
    return fromstring(content)
  except Exception as e:
    print(response)
    raise e