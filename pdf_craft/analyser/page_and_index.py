from typing import Iterable
from xml.etree.ElementTree import Element
from .llm import LLM
from .asset_matcher import search_asset_tags, AssetMatcher, ASSET_TAGS
from .utils import encode_response


def analyse_page(llm: LLM, raw_page_xml: Element, previous_page_xml: Element | None):
  if previous_page_xml is None:
    raw_page_xml.set("previous-page", "null")
  elif previous_page_xml.tag == "index":
    raw_page_xml.set("previous-page", "index")
  else:
    raw_page_xml.set("previous-page", "page")

  asset_matcher = AssetMatcher().register_raw_xml(raw_page_xml)
  raw_page_xml = _clean_hash_from_assets(raw_page_xml)
  response = llm.request("page", raw_page_xml, {})
  response_xml: Element = encode_response(response)

  if response_xml.tag == "index":
    index_xml = Element("index")
    index_xml.extend(raw_page_xml)
    return index_xml

  elif response_xml.tag == "page":
    asset_matcher.add_asset_hashes_for_xml(response_xml)
    _collect_for_citation(response_xml)
    return response_xml

def _clean_hash_from_assets(xml: Element) -> Element:
  for asset_xml in search_asset_tags(xml):
    asset_xml.attrib = {}
  return xml

def analyse_index(llm: LLM, raw_page_xmls: Iterable[Element]) -> tuple[Element | None, int, int]:
  raw_index_pages: list[tuple[int, Element]] = []
  for i, raw_page_xml in enumerate(raw_page_xmls):
    if raw_page_xml.tag == "index":
      raw_index_pages.append((i, raw_page_xml))

  if len(raw_index_pages) == 0:
    return None, -1, -1

  raw_page_xml = Element("index")
  for i, (_, raw_index_xml) in enumerate(raw_index_pages):
    raw_index_xml.set("page-index", str(i + 1))
    raw_page_xml.append(raw_index_xml)

  response = llm.request("index", raw_page_xml, {})
  response_xml: Element = encode_response(response)

  start_page_index = min(i + 1 for i, _ in raw_index_pages)
  end_page_index = max(i + 1 for i, _ in raw_index_pages)

  return response_xml, start_page_index, end_page_index

def _collect_for_citation(response_root: Element):
  citation: Element | None = None
  for child in list(response_root):
    if child.tag == "citation":
      citation = child
    elif citation is not None:
      citation.append(child)
      response_root.remove(child)

def _handle_asset_tags(parent: Element):
  pre_asset: Element | None = None
  asset_captions: list[Element] = []
  for child in parent:
    if child.tag not in ASSET_TAGS:
      if child.tag == "citation":
        _handle_asset_tags(child)
      if pre_asset is not None and \
         child.tag == f"{pre_asset.tag}-caption":
        for caption_child in child:
          pre_asset.append(caption_child)
        asset_captions.append(child)
      pre_asset = None
    if "hash" in child.attrib:
      pre_asset = child
      yield child.get("hash")
  for asset_caption in asset_captions:
    parent.remove(asset_caption)
