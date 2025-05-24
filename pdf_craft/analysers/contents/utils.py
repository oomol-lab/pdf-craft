from xml.etree.ElementTree import Element
from ..data import Paragraph
from ..utils import search_xml_children


def normalize_layout_xml(paragraph: Paragraph) -> Element | None:
  merged_layout: Element | None = None
  for layout in paragraph.layouts:
    if merged_layout is None:
      merged_layout = layout.to_xml()
    else:
      for line in layout.lines:
        merged_layout.append(line.to_xml())

  if merged_layout is not None:
    merged_layout.attrib = {}
    for child, _ in search_xml_children(merged_layout):
      if child.tag == "line":
        child.attrib = {}

  return merged_layout