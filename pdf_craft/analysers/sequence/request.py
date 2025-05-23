from __future__ import annotations
from typing import Generator
from xml.etree.ElementTree import Element

from ...xml import encode_friendly
from ...llm import LLM


_ASSET_TAGS = ("figure", "table", "formula")

class SequenceRequest:
  def __init__(self):
    self.begin: int = -1
    self.end: int = -1
    self.children: list[Element] = []
    self.asset_datas: list[_AssetData] = []

  def append(self, page_index: int, raw_page: RawPage):
    self.children.extend(raw_page.children)
    self.asset_datas.extend(raw_page.asset_datas)
    self.end = page_index
    if self.begin == -1:
      self.begin = page_index

  def to_xml(self) -> Element:
    element = Element("request")
    next_line_id: int = 1
    for layout_element in self.children:
      element.append(layout_element)
      for line_element in layout_element:
        if line_element.tag == "line":
          line_element.set("id", str(next_line_id))
          next_line_id += 1
    return element

class RawPage:
  def __init__(self, raw_element: Element, page_index: int) -> None:
    self.asset_datas: list[_AssetData] = []
    self.children: list[Element] = []
    raw_element.set("page-index", str(page_index))

    for layout_element, asset_captions in self._handle_layout_elements(raw_element):
      if layout_element.tag in _ASSET_TAGS:
        asset_data = _AssetData(layout_element, asset_captions)
        self.asset_datas.append(asset_data)
        self.children.append(asset_data.element)
      else:
        self.children.append(layout_element)

    for layout_element in self.children:
      for line_element in layout_element:
        if line_element.tag == "line":
          # just as a placeholder, so that the tokens are as consistent as possible with the final output
          line_element.set("id", "X")

  def _handle_layout_elements(self, raw_element: Element) -> Generator[tuple[Element, list[Element]], None, None]:
    last_asset: Element | None = None
    last_captions: list[Element] = []

    for layout_element in raw_element:
      if layout_element.tag in _ASSET_TAGS:
        last_asset = layout_element
        yield layout_element, []

      elif last_asset is not None and\
           layout_element.tag == f"{last_asset.tag}-caption":
        last_captions.append(layout_element)

      else:
        yield layout_element, last_captions
        last_captions = []

  def tokens_count(self, llm: LLM) -> int:
    tokens_count: int = 0
    for element in self.children:
      text = encode_friendly(element)
      tokens_count += len(llm.encode_tokens(text))
    return tokens_count

class _AssetData:
  def __init__(self, element: Element, captions: list[Element]):
    self.element = element
    self.captions: list[Element] = captions
    self.hash = element.attrib.pop("hash", "")

    if element.tag == "figure":
      element.clear()
      element.append(self._create_line("[[OCR recognized figure here]]"))

    elif element.tag == "table":
      element.clear()
      element.append(self._create_line("[[OCR recognized table here]]"))

    elif element.tag == "formula":
      latex_element = element.find("latex")
      line_text = "[[OCR recognized formula here]]"
      if latex_element is not None and latex_element.text:
        line_text = latex_element.text

      element.clear()
      element.append(self._create_line(line_text))

  def _create_line(self, text: str) -> Element:
    line_element = Element("line")
    line_element.text = text
    line_element.attrib["confidence"] = "1.0"
    return line_element