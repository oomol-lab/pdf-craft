import os
import fitz

from html import escape
from hashlib import sha256
from typing import Generator
from PIL.Image import Image
from xml.etree.ElementTree import Element
from ..pdf import PDFPageExtractor, Block, Text, TextBlock, AssetBlock, TextKind, AssetKind
from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .asset_matcher import AssetMatcher, ASSET_TAGS


def extract_ocr_page_xmls(
    extractor: PDFPageExtractor,
    pdf_path: str,
    expected_page_indexes: set[int],
    cover_path: str,
    assets_dir_path: str,
    report_step: AnalysingStepReport | None,
    report_progress: AnalysingProgressReport | None,
  ) -> Generator[Element, None, None]:

  with fitz.open(pdf_path) as pdf:
    if report_step is not None:
      report_step(
        AnalysingStep.OCR,
        pdf.page_count - len(expected_page_indexes),
      )
    for i, blocks, image in extractor.extract_enumerated_blocks_and_image(
      pdf=pdf,
      page_indexes=(i for i in range(pdf.page_count) if i not in expected_page_indexes),
    ):
      if i == 0:
        image.save(cover_path)

      page_xml = _transform_page_xml(blocks)
      _bind_and_save_assets(
        root=page_xml,
        blocks=blocks,
        assets_dir_path=assets_dir_path,
      )
      yield i, page_xml

      if report_progress is not None:
        report_progress(i + 1)

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

def _bind_and_save_assets(root: Element, blocks: list[Block], assets_dir_path: str):
  asset_matcher = AssetMatcher()
  images: dict[str, Image] = {}
  for block in blocks:
    if isinstance(block, AssetBlock):
      hash = _block_image_hash(block)
      images[hash] = block.image
      asset_matcher.register_hash(block.kind, hash)
  asset_matcher.add_asset_hashes_for_xml(root)

  for hash in _handle_asset_tags(root):
    image: Image | None = images.get(hash, None)
    if image is not None:
      file_path = os.path.join(assets_dir_path, f"{hash}.png")
      if not os.path.exists(file_path):
        image.save(file_path, "PNG")

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
