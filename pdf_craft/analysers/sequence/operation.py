from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import Element

from ..utils import read_xml_file, xml_files, Context
from ..data import (
  Paragraph,
  ParagraphType,
  Caption,
  FormulaLayout,
  AssetLayout,
  Line,
  Layout,
  LayoutKind,
)


def read_paragraphs(dir_path: Path, name: str = "paragraph") -> Generator[Paragraph, None, None]:
  for file_path, _name, page_index, order_index in xml_files(dir_path):
    if name == _name:
      element = read_xml_file(file_path)
      yield decode_paragraph(element, page_index, order_index)

def decode_paragraph(element: Element, page_index: int, order_index: int):
  return Paragraph(
    type=ParagraphType(element.get("type")),
    page_index=page_index,
    order_index=order_index,
    layouts=[decode_layout(e) for e in element],
  )

def decode_layout(element: Element) -> Layout:
  id: str = element.get("id")
  kind = LayoutKind(element.tag)
  page_index, order_index = id.split("/", maxsplit=1)
  caption_lines: list[Line] = []
  body_lines: list[Line] = [
    _decode_line(line_element)
    for line_element in element
    if line_element.tag == "line"
  ]
  for child in element:
    if child.tag != "caption":
      continue
    for line_element in child:
      if line_element.tag != "line":
        continue
      caption_lines.append(_decode_line(line_element))

  if kind == LayoutKind.FORMULA:
    hash_hex = element.get("hash", "")
    latex_element = element.find("latex")
    latex_text: str = ""
    if latex_element is not None:
      latex_text = latex_element.text or ""

    return FormulaLayout(
      kind=kind,
      hash=bytes.fromhex(hash_hex),
      page_index=int(page_index),
      order_index=int(order_index),
      caption=Caption(lines=caption_lines),
      lines=body_lines,
      latex=latex_text,
    )
  elif kind in (LayoutKind.FIGURE, LayoutKind.TABLE):
    hash_hex = element.get("hash", "")
    return AssetLayout(
      kind=kind,
      hash=bytes.fromhex(hash_hex),
      page_index=int(page_index),
      order_index=int(order_index),
      caption=Caption(lines=caption_lines),
      lines=body_lines,
    )
  else:
    return Layout(
      kind=kind,
      page_index=int(page_index),
      order_index=int(order_index),
      caption=Caption(lines=caption_lines),
      lines=body_lines,
    )

def _decode_line(element: Element) -> Line:
  return Line(
    text=(element.text or "").strip(),
    confidence=element.get("confidence", "1.0"),
  )

class ParagraphWriter:
  def __init__(self, context: Context, dir_path: Path, name: str = "paragraph"):
    self._name: str = name
    self._context: Context = context
    self._dir_path: Path = dir_path

    if not self._dir_path.exists():
      self._dir_path.mkdir(parents=True)
    if not self._dir_path.is_dir():
      raise ValueError(f"Path {self._dir_path} is not a directory")

  def write(self, paragraph: Paragraph) -> None:
    file_name = f"{self._name}_{paragraph.page_index}_{paragraph.order_index}.xml"
    self._context.write_xml_file(
      file_path=self._dir_path / file_name,
      xml=paragraph.to_xml(),
    )