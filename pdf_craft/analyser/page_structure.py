import io
import os
import re

from html import escape
from hashlib import sha256
from xml.etree.ElementTree import fromstring, tostring, XML, Element
from ..pdf import Block, Text, TextBlock, AssetBlock, TextKind, AssetKind
from .llm import LLM
from .asset_matcher import AssetMatcher, ASSET_TAGS


def structure(llm: LLM, blocks: list[Block], output_file_path: str, assets_dir_path: str):
  raw_page_xml = _get_page_xml(blocks)
  page_xml = llm.request("page_structure", raw_page_xml)
  root: XML = fromstring(_preprocess_page_xml(page_xml))

  _match_assets(root, blocks, assets_dir_path)
  with open(output_file_path, "wb") as file:
    file.write(tostring(root, encoding="utf-8"))

def _get_page_xml(blocks: list[Block]) -> str:
  buffer = io.StringIO()
  buffer.write("<page>\n")
  for block in blocks:
    _write_block(buffer, block)
  buffer.write("</page>\n")
  return buffer.getvalue()

def _preprocess_page_xml(page_xml: str) -> str:
  matches = re.findall(r"<page>.*</page>", page_xml, re.DOTALL)
  if matches and len(matches) > 0:
    return matches[0].replace("&", "&amp;")
  else:
    raise ValueError("No page tag found in LLM response")

def _match_assets(root: XML, blocks: list[Block], assets_dir_path: str):
  asset_matcher = AssetMatcher()
  for block in blocks:
    if isinstance(block, AssetBlock):
      hash = _save_image(assets_dir_path, block)
      asset_matcher.register_hash(block.kind, hash)
  asset_matcher.add_asset_hashes_for_xml(root)
  _handle_asset_tags(root)

def _write_block(buffer: io.StringIO, block: Block):
  if isinstance(block, TextBlock):
    tag_name: str
    if block.kind == TextKind.TITLE:
      tag_name = "title"
    elif block.kind == TextKind.PLAIN_TEXT:
      tag_name = "text"
    elif block.kind == TextKind.ABANDON:
      tag_name = "abandon"

    buffer.write("<")
    buffer.write(tag_name)

    if block.kind == TextKind.PLAIN_TEXT:
      buffer.write(" indent=")
      buffer.write("\"true\"" if block.has_paragraph_indentation else "\"false\"")
      buffer.write(" touch-end=")
      buffer.write("\"true\"" if block.last_line_touch_end else "\"false\"")

    buffer.write(">\n")

    _write_texts(buffer, block.texts)

    buffer.write("</")
    buffer.write(tag_name)
    buffer.write(">\n")

  elif isinstance(block, AssetBlock):
    tag_name: str
    if block.kind == AssetKind.FIGURE:
      tag_name = "figure"
    elif block.kind == AssetKind.TABLE:
      tag_name = "table"
    elif block.kind == AssetKind.FORMULA:
      tag_name = "formula"

    buffer.write("<")
    buffer.write(tag_name)
    buffer.write("/>\n")

    if len(block.texts) > 0:
      buffer.write("<")
      buffer.write(tag_name)
      buffer.write("-caption>\n")

      _write_texts(buffer, block.texts)

      buffer.write("</")
      buffer.write(tag_name)
      buffer.write("-caption>\n")

def _write_texts(buffer: io.StringIO, texts: list[Text]):
  for text in texts:
    content = text.content.replace("\n", " ")
    content = escape(content.strip())
    buffer.write("<line confidence=")
    buffer.write("{:.2f}".format(text.rank))
    buffer.write(">")
    buffer.write(content)
    buffer.write("</line>\n")

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
