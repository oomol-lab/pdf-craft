import os
import re

from html import escape
from hashlib import sha256
from typing import Iterable
from xml.etree.ElementTree import fromstring, tostring, XML, Element
from ..pdf import Block, Text, TextBlock, AssetBlock, TextKind, AssetKind
from .llm import LLM
from .asset_matcher import AssetMatcher, ASSET_TAGS


def preliminary_analyse(llm: LLM, page_dir_path: str, assets_dir_path: str, blocks_matrix: Iterable[list[Block]]):
  for i, blocks in enumerate(blocks_matrix):
    raw_page_xml = _transform_page_xml(blocks)
    raw_data = tostring(raw_page_xml, encoding="unicode")
    response = llm.request("page_structure", raw_data)
    root: Element | None = _process_response_page_xml(response)
    if root is not None:
      _match_assets(root, blocks, assets_dir_path)
      file_path = os.path.join(page_dir_path, f"page_{i + 1}.xml")
      with open(file_path, "wb") as file:
        file.write(tostring(root, encoding="utf-8"))

def _process_response_page_xml(response: str) -> Element | None:
  if "<index/>" in response:
    return None
  matches = re.findall(r"<page>.*</page>", response, re.DOTALL)
  if not matches or len(matches) == 0:
    raise ValueError("No page tag found in LLM response")
  xml_content = matches[0].replace("&", "&amp;")
  return fromstring(xml_content)

def _transform_page_xml(blocks: list[Block]) -> Element:
  root = Element("page")
  for block in blocks:
    if isinstance(block, TextBlock):
      tag_name: str
      if block.kind == TextKind.TITLE:
        tag_name = "title"
      elif block.kind == TextKind.PLAIN_TEXT:
        tag_name = "text"
      elif block.kind == TextKind.ABANDON:
        tag_name = "abandon"

      text_dom = Element(tag_name)
      if block.kind == TextKind.PLAIN_TEXT:
        text_dom.set("indent", "true" if block.has_paragraph_indentation else "false")
        text_dom.set("touch-end", "true" if block.last_line_touch_end else "false")

      _extends_line_doms(text_dom, block.texts)
      root.append(text_dom)

    elif isinstance(block, AssetBlock):
      tag_name: str
      if block.kind == AssetKind.FIGURE:
        tag_name = "figure"
      elif block.kind == AssetKind.TABLE:
        tag_name = "table"
      elif block.kind == AssetKind.FORMULA:
        tag_name = "formula"

      root.append(Element(tag_name))
      if len(block.texts) > 0:
        caption_dom = Element(f"{tag_name}-caption")
        _extends_line_doms(caption_dom, block.texts)
        root.append(caption_dom)
  return root

def _extends_line_doms(parent: Element, texts: list[Text]):
  for text in texts:
    content = text.content.replace("\n", " ")
    content = escape(content.strip())
    line_dom = Element("line")
    line_dom.set("confidence", "{:.2f}".format(text.rank))
    line_dom.text = content
    parent.append(line_dom)

def _match_assets(root: XML, blocks: list[Block], assets_dir_path: str):
  asset_matcher = AssetMatcher()
  for block in blocks:
    if isinstance(block, AssetBlock):
      hash = _save_image(assets_dir_path, block)
      asset_matcher.register_hash(block.kind, hash)
  asset_matcher.add_asset_hashes_for_xml(root)
  _handle_asset_tags(root)

def _save_image(assets_dir_path: str, block: AssetBlock) -> None:
  hash = sha256()
  hash.update(block.image.tobytes())
  file_hash = hash.hexdigest()
  file_path = os.path.join(assets_dir_path, f"{file_hash}.png")
  if not os.path.exists(file_path):
    block.image.save(file_path, "PNG")
  return file_hash

def _handle_asset_tags(parent: Element):
  children: list[Element] = []
  pre_asset: Element | None = None

  for child in parent:
    if child.tag not in ASSET_TAGS:
      if child.tag == "citation":
        _handle_asset_tags(child)
      if pre_asset is not None and \
         child.tag == f"{pre_asset.tag}-caption":
        for caption_child in child:
          pre_asset.append(caption_child)
      else:
        children.append(child)
      pre_asset = None
    if "hash" in child.attrib:
      pre_asset = child
      children.append(child)
