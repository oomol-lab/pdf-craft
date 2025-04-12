import os
import fitz

from html import escape
from hashlib import sha256
from typing import Generator, Iterable
from PIL.Image import Image
from xml.etree.ElementTree import Element

from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .asset_matcher import search_asset_tags, AssetMatcher, AssetKind
from ..pdf import (
  PDFPageExtractor,
  Block,
  Text,
  TextKind,
  TextBlock,
  TableBlock,
  TableFormat,
  FigureBlock,
  FormulaBlock,
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

    elif isinstance(block, TableBlock):
      _append_asset_dom(root, block, "table")

    elif isinstance(block, FormulaBlock):
      _append_asset_dom(root, block, "formula")

    elif isinstance(block, FigureBlock):
      _append_asset_dom(root, block, "figure")

  return root

def _append_asset_dom(root: Element, block: Block, tag_name: str):
  root.append(Element(tag_name))
  if len(block.texts) > 0:
    caption_dom = Element(f"{tag_name}-caption")
    _extends_line_doms(caption_dom, block.texts)
    root.append(caption_dom)

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

  def register_image_and_get_hash(image: Image):
    hash256 = sha256()
    hash256.update(image.tobytes())
    hash = hash256.hexdigest()
    images[hash] = image
    return hash

  def create_children(tag_name: str, text: str) -> Iterable[Element]:
    child = Element(tag_name)
    child.text = text
    return (child,)

  for block in blocks:
    kind: AssetKind | None = None
    hash: str | None = None
    children: Iterable[Element] | None = None

    if isinstance(block, TableBlock):
      kind = AssetKind.TABLE
      hash = register_image_and_get_hash(AssetKind.FORMULA, block.image)
      if block.format == TableFormat.LATEX:
        children = create_children("latex", block.content)
      elif block.format == TableFormat.MARKDOWN:
        children = create_children("markdown", block.content)
      elif block.format == TableFormat.HTML:
        children = create_children("html", block.content)

    elif isinstance(block, FormulaBlock):
      kind = AssetKind.FORMULA
      hash = register_image_and_get_hash(AssetKind.FORMULA, block.image)
      if block.content is not None:
        children = create_children("latex", block.content)

    elif isinstance(block, FigureBlock):
      kind = AssetKind.FIGURE
      hash = register_image_and_get_hash(AssetKind.FORMULA, block.image)

    if kind is not None:
      asset_matcher.register_hash(
        kind=kind,
        hash=hash,
        children=children,
      )

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
