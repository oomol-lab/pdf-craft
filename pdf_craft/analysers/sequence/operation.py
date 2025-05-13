from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import Element

from ..context import Context
from .paragraph import Paragraph, ParagraphType, Line, Layout, LayoutKind


def read_paragraphs(dir_path: Path, name: str = "paragraph"):
  context: Context[None] = Context(dir_path, lambda: None)
  for file_path, _name, page_index, order_index in context.xml_files(dir_path):
    if name != _name:
      continue
    root = context.read_xml_file(file_path)
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
      xml=self._to_root(paragraph),
    )

  def _to_root(self, paragraph: Paragraph) -> Element:
    root = Element(self._name)
    root.set("type", paragraph.type.value)
    for layout in paragraph.layouts:
      layout_element = Element(layout.kind.value)
      layout_element.set("id", layout.id)
      for line in layout.lines:
        line_element = Element("line")
        line_element.text = line.text
        line_element.set("confidence", str(line.confidence))
        layout_element.append(line_element)
      root.append(layout_element)
    return root