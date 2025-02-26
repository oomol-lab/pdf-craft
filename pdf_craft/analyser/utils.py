import os
import re

from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

def read_xml_files(dir_path: str, enable_kinds: Iterable[str]) -> Generator[tuple[Element, str, str, int, int], None, None]:
  for file_name in os.listdir(dir_path):
    file_path = os.path.join(dir_path, file_name)
    matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
    if not matches:
      continue

    root: Element
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

    with open(file_path, "r", encoding="utf-8") as file:
      root = fromstring(file.read())

    yield root, file_name, kind, int(index1), int(index2)