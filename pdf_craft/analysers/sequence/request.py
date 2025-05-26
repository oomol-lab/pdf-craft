from __future__ import annotations
from typing import Generator
from xml.etree.ElementTree import Element

from ...xml import encode_friendly
from ...llm import LLM
from ..data import ASSET_LAYOUT_KINDS


class SequenceRequest:
  def __init__(self):
    self.begin: int = -1
    self.end: int = -1
    self.raw_pages: list[RawPage] = []

  def append(self, page_index: int, raw_page: RawPage):
    self.raw_pages.append(raw_page)
    self.end = page_index
    if self.begin == -1:
      self.begin = page_index

  def raw_page(self, page_index: int) -> RawPage | None:
    for raw_page in self.raw_pages:
      if raw_page.page_index == page_index:
        return raw_page
    return None

  def inject_ids_and_get_xml(self) -> Element:
    next_line_id: int = 1
    request_element = Element("request")

    for raw_page in self.raw_pages:
      page_element = Element("page")
      page_element.set("page-index", str(raw_page.page_index))
      request_element.append(page_element)
      next_asset_index: int = 0

      for layout_element in raw_page.children:
        page_element.append(layout_element)
        if layout_element.tag not in ASSET_LAYOUT_KINDS:
          for line_element in layout_element:
            if line_element.tag == "line":
              line_element.set("id", str(next_line_id))
              next_line_id += 1
        else:
          asset = raw_page.asset_datas[next_asset_index]
          asset_line = next((c for c in layout_element if c.tag == "line"), None)
          next_asset_index += 1
          if asset_line is not None:
            asset_line.set("id", str(next_line_id))
            asset.line_id = next_line_id
            next_line_id += 1

    return request_element

class RawPage:
  def __init__(self, raw_element: Element, page_index: int) -> None:
    self.asset_datas: list[_AssetData] = []
    self.children: list[Element] = []
    self.page_index: int = page_index

    for layout_element, asset_captions in self._handle_layout_elements(raw_element):
      if layout_element.tag in ASSET_LAYOUT_KINDS:
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
    asset_and_captions: tuple[Element, list[Element]] | None = None

    for layout_element in raw_element:
      if layout_element.tag in ASSET_LAYOUT_KINDS:
        if asset_and_captions is not None:
          asset, captions = asset_and_captions
          yield asset, captions
        asset_and_captions = (layout_element, [])

      else:
        did_append_as_caption = False
        if asset_and_captions is not None:
          asset, captions = asset_and_captions
          if layout_element.tag == f"{asset.tag}-caption":
            captions.append(layout_element)
            did_append_as_caption = True

        if not did_append_as_caption:
          if asset_and_captions is not None:
            asset, captions = asset_and_captions
            asset_and_captions = None
            yield asset, captions
          yield layout_element, []
          asset_and_captions = None

    if asset_and_captions is not None:
      asset, captions = asset_and_captions
      yield asset, captions

  def tokens_count(self, llm: LLM) -> int:
    tokens_count: int = 0
    for element in self.children:
      text = encode_friendly(element)
      tokens_count += len(llm.encode_tokens(text))
    return tokens_count

  def inject_assets(self, line_ids: list[int]) -> Generator[tuple[int, Element | None], None, None]:
    if len(line_ids) <= 1:
      for line_id in line_ids:
        yield line_id, None
      return

    pre_line_id: int | None = None
    asset_datas = [
      asset for _, asset in sorted(
        [
          (asset.line_id, asset)
          for asset in self.asset_datas
          if asset.line_id is not None
        ],
        key=lambda x: x[0],
      )
    ]
    for line_id in sorted(line_ids):
      did_yield_this = False
      for asset in asset_datas:
        if asset.line_id == line_id:
          yield line_id, asset.to_saved_xml()
          did_yield_this = True
          break
        if pre_line_id is not None and \
           pre_line_id < asset.line_id < line_id:
          yield asset.line_id, asset.to_saved_xml()
      if not did_yield_this:
        yield line_id, None
      pre_line_id = line_id

  def assets_in_range(
        self,
        after_line_id: int | None = None,
        before_line_id: int | None = None,
      ) -> Generator[Element, None, None]:
    for asset in self.asset_datas:
      line_id = asset.line_id
      if line_id is None:
        continue
      if after_line_id is not None and line_id <= after_line_id:
        continue
      if before_line_id is not None and line_id >= before_line_id:
        break
      yield asset.to_saved_xml()

class _AssetData:
  def __init__(self, element: Element, captions: list[Element]):
    self.element = element
    self.line_id: int | None = None
    self._captions: list[Element] = captions
    self._hash: str | None = element.attrib.pop("hash", None)

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

  def to_saved_xml(self) -> Element:
    asset_element = Element(self.element.tag)
    if asset_element.tag == "formula":
      line_element = self.element.find("line")
      if line_element is not None:
        latex_element = Element("latex")
        latex_element.text = line_element.text
        asset_element.append(latex_element)

    for raw_caption_element in self._captions:
      caption_element = Element("caption")
      caption_element.extend(raw_caption_element)
      asset_element.append(caption_element)

    if self._hash is not None:
      asset_element.set("hash", self._hash)
    return asset_element

  def _create_line(self, text: str) -> Element:
    line_element = Element("line")
    line_element.text = text
    line_element.attrib["confidence"] = "1.0"
    return line_element