import os

from html import escape
from hashlib import sha256
from typing import Iterable
from PIL.Image import Image
from xml.etree.ElementTree import tostring, XML, Element
from ..pdf import Block, Text, TextBlock, AssetBlock, TextKind, AssetKind
from .llm import LLM
from .asset_matcher import AssetMatcher, ASSET_TAGS
from .utils import encode_response


def preliminary_analyse(llm: LLM, page_dir_path: str, assets_dir_path: str, blocks_matrix: Iterable[list[Block]]):
  is_prev_index: bool = False
  index_pages: list[tuple[int, Element]] = []

  for i, blocks in enumerate(blocks_matrix):
    raw_page_xml = _transform_page_xml(blocks)
    if i == 0:
      raw_page_xml.set("previous-page", "null")
    elif is_prev_index:
      raw_page_xml.set("previous-page", "index")
    else:
      raw_page_xml.set("previous-page", "page")

    response = llm.request("preliminary", raw_page_xml, {})
    response_root: Element = encode_response(response)

    if response_root.tag == "index":
      is_prev_index = True
      index_pages.append((i, raw_page_xml))
      raw_page_xml.attrib = {}

    if response_root.tag == "page":
      _match_assets(response_root, blocks, assets_dir_path)
      _collect_for_citation(response_root)
      file_path = os.path.join(page_dir_path, f"page_{i + 1}.xml")
      is_prev_index = False
      with open(file_path, "wb") as file:
        file.write(tostring(response_root, encoding="utf-8"))

  if len(index_pages) == 0:
    return

  raw_page_xml = Element("index")
  for i, (_, index_xml) in enumerate(index_pages):
    index_xml.set("page-index", str(i + 1))
    raw_page_xml.append(index_xml)

  response = llm.request("index", raw_page_xml, {})
  response_root: Element = encode_response(response)

  start_page_index = min(i + 1 for i, _ in index_pages)
  end_page_index = max(i + 1 for i, _ in index_pages)
  file_path = os.path.join(page_dir_path, f"index_{start_page_index}_{end_page_index}.xml")

  with open(file_path, "wb") as file:
    file.write(tostring(response_root, encoding="utf-8"))

def _transform_page_xml(blocks: list[Block]) -> Element:
  root = Element("page")
  for block in blocks:
    if isinstance(block, TextBlock):
      tag_name: str
      if block.kind == TextKind.TITLE:
        tag_name = "headline"
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
  images: dict[str, Image] = {}
  for block in blocks:
    if isinstance(block, AssetBlock):
      hash = _block_image_hash(block)
      images[hash] = block.image
      asset_matcher.register_hash(block.kind, hash)
  asset_matcher.add_asset_hashes_for_xml(root)

  for hash in _handle_asset_tags(root):
    image = images.get(hash, None)
    if image is not None:
      file_path = os.path.join(assets_dir_path, f"{hash}.png")
      if not os.path.exists(file_path):
        image.save(file_path, "PNG")

def _collect_for_citation(response_root: Element):
  citation: Element | None = None
  for child in list(response_root):
    if child.tag == "citation":
      citation = child
    elif citation is not None:
      citation.append(child)
      response_root.remove(child)

def _block_image_hash(block: AssetBlock) -> str:
  hash = sha256()
  hash.update(block.image.tobytes())
  return hash.hexdigest()

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
