import fitz

from pathlib import Path
from typing import Generator, Iterable
from PIL.Image import Image
from xml.etree.ElementTree import fromstring, Element, ParseError

from ...utils import sha256_hash
from .asset_matcher import search_asset_tags, AssetMatcher, AssetKind
from ..reporter import Reporter
from ...pdf import (
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
    pdf_path: Path,
    expected_page_indexes: set[int],
    cover_path: Path,
    assets_dir_path: Path,
    reporter: Reporter,
  ) -> Generator[tuple[int, Element], None, None]:

  with fitz.open(pdf_path) as pdf:
    reporter.set(max_count=pdf.page_count)
    reporter.set_progress(len(expected_page_indexes))

    for i, blocks, image in extractor.extract_enumerated_blocks_and_image(
      pdf=pdf,
      page_indexes=(i for i in range(pdf.page_count) if i not in expected_page_indexes),
    ):
      if image and i == 0:
        image.save(cover_path)

      page_xml = _transform_page_xml(blocks)
      _migrate_expressions_and_save_images(
        root=page_xml,
        blocks=blocks,
        assets_dir_path=assets_dir_path,
      )
      reporter.increment()
      yield i, page_xml

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
      else:
        raise ValueError(f"Unknown text kind: {block.kind}")

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
    line_dom = Element("line")
    line_dom.set("confidence", "{:.2f}".format(text.rank))
    line_dom.text = text.content.replace("\n", " ")
    parent.append(line_dom)

def _migrate_expressions_and_save_images(root: Element, blocks: list[Block], assets_dir_path: Path):
  asset_matcher = AssetMatcher()
  images: dict[str, Image] = {}

  def register_image_and_get_hash(image: Image):
    hash = sha256_hash(image.tobytes())
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
      hash = register_image_and_get_hash(block.image)
      if block.format == TableFormat.LATEX:
        children = create_children("latex", block.content)
      elif block.format == TableFormat.MARKDOWN:
        children = create_children("markdown", block.content)
      elif block.format == TableFormat.HTML:
        try:
          table_element = fromstring(block.content)
          table_content = Element("html")
          table_content.append(table_element)
          children = (table_content,)
        except ParseError as err:
          print("warn: parsed table HTML failed: ", err)
          pass

    elif isinstance(block, FormulaBlock):
      kind = AssetKind.FORMULA
      hash = register_image_and_get_hash(block.image)
      if block.content is not None:
        children = create_children("latex", block.content)

    elif isinstance(block, FigureBlock):
      kind = AssetKind.FIGURE
      hash = register_image_and_get_hash(block.image)

    if kind is not None:
      asset_matcher.register_virtual_dom(
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
    file_path = assets_dir_path / f"{hash}.png"
    if file_path.exists():
      continue
    image.save(file_path, "PNG")