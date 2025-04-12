import os
import fitz

from html import escape
from hashlib import sha256
from typing import Generator
from PIL.Image import Image
from xml.etree.ElementTree import Element

from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .asset_matcher import search_asset_tags, AssetMatcher, AssetKind
from ..pdf import (
  PDFPageExtractor,
  Block,
  Text,
  TextBlock,
  FormulaBlock,
  AssetBlock,
  TextKind,
  AssetKind as PDFAssetKind,
)


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
      _bind_hashes_and_save_images(
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

    elif isinstance(block, FormulaBlock):
      formula_dom = Element("formula")
      root.append(formula_dom)
      if len(block.texts) > 0:
        caption_dom = Element("formula-caption")
        _extends_line_doms(caption_dom, block.texts)
        root.append(caption_dom)

    elif isinstance(block, AssetBlock):
      tag_name: str
      if block.kind == PDFAssetKind.FIGURE:
        tag_name = "figure"
      elif block.kind == PDFAssetKind.TABLE:
        tag_name = "table"

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

def _bind_hashes_and_save_images(root: Element, blocks: list[Block], assets_dir_path: str):
  asset_matcher = AssetMatcher()
  images: dict[str, Image] = {}

  def register_image(kind: AssetKind, image: Image):
    hash256 = sha256()
    hash256.update(image.tobytes())
    hash = hash256.hexdigest()
    images[hash] = image
    asset_matcher.register_hash(kind=kind, hash=hash)

  for block in blocks:
    if isinstance(block, FormulaBlock):
      if isinstance(block.content, str):
        latex = Element("latex")
        latex.text = block.content
        asset_matcher.register_hash(
          kind=AssetKind.FORMULA,
          children=(latex,),
        )
      else:
        register_image(AssetKind.FORMULA, block.content)

    elif isinstance(block, AssetBlock):
      kind: AssetKind
      if block.kind == PDFAssetKind.FIGURE:
        kind = AssetKind.FIGURE
      elif block.kind == PDFAssetKind.TABLE:
        kind = AssetKind.TABLE
      else:
        raise ValueError(f"Unknown asset kind: {block.kind}")
      register_image(kind, block.image)

  asset_matcher.recover_asset_doms_for_xml(root)

  for asset_dom in search_asset_tags(root):
    hash = asset_dom.get("hash", None)
    if hash is None:
      continue
    image: Image | None = images.get(hash, None)
    if image is None:
      continue
    file_path = os.path.join(assets_dir_path, f"{hash}.png")
    if os.path.exists(file_path):
      continue
    image.save(file_path, "PNG")
