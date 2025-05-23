from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import Element

from ..data import Paragraph, ParagraphType, Line, Layout, LayoutKind
from ..utils import read_xml_file, xml_files, Context


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
  return Layout(
    kind=kind,
    page_index=int(page_index),
    order_index=int(order_index),
    lines=[
      Line(
        text=(line_element.text or "").strip(),
        confidence=line_element.get("confidence"),
      )
      for line_element in element
    ],
  )

class ParagraphWriter:
  def __init__(self, dir_path: Path, name: str = "paragraph"):
    self._name: str = name
    self._context: Context[None] = Context(dir_path, lambda: None)

  def write(self, paragraph: Paragraph) -> None:
    file_name = f"{self._name}_{paragraph.page_index}_{paragraph.order_index}.xml"
    self._context.write_xml_file(
      file_path=self._context.path / file_name,
      xml=paragraph.xml(),
    )