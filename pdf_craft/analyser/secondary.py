import os
import re

from enum import Enum
from natsort import natsorted
from dataclasses import dataclass
from xml.etree.ElementTree import fromstring, XML
from .llm import LLM


@dataclass
class PageInfo:
  file_name: str
  page_index: int
  main_tokens: int
  citation_tokens: int
  has_citation: bool

@dataclass
class TextBorder(Enum):
  pass

class SecondaryAnalyser:
  def __init__(self, llm: LLM, dir_path: str):
    self._llm: LLM = llm
    self._assets_dir_path = os.path.join(dir_path, "assets")
    pages_dir_path: str = os.path.join(dir_path, "pages")
    file_names = natsorted(os.listdir(pages_dir_path))
    file_names = [f for f in file_names if re.match(r"^page_\d+\.xml$", f)]

    for page_index, file_name in enumerate(file_names):
      file_path = os.path.join(pages_dir_path, file_name)
      with open(file_path, "r", encoding="utf-8") as file:
        root: XML = fromstring(file.read())
        self._parse(file_name, page_index, root)

  def _parse(self, file_name: str, page_index: int, root: XML):
    print(page_index, root)