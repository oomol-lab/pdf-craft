from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import Element

from ..context import Context
from ..utils import read_xml_file, xml_files
from .paragraph import Paragraph, ParagraphType, Line, Layout, LayoutKind


def read_paragraphs(dir_path: Path, name: str = "paragraph") -> Generator[Paragraph, None, None]:
  for file_path, _name, page_index, order_index in xml_files(dir_path):
    if name != _name:
      continue
    root = read_xml_file(file_path)
    yield Paragraph(
      type=ParagraphType(root.get("type")),
      page_index=page_index,
      order_index=order_index,
      layouts=list(_read_layouts(root)),
    )

def _read_layouts(root: Element) -> Generator[Layout, None, None]:
  for layout_element in root:
    id: str = layout_element.get("id")
    kind = LayoutKind(layout_element.tag)
    page_index, order_index = id.split("/", maxsplit=1)
    yield Layout(
      kind=kind,
      page_index=int(page_index),
      order_index=int(order_index),
      lines=[
        Line(
          text=(line_element.text or "").strip(),
          confidence=line_element.get("confidence"),
        )
        for line_element in layout_element
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