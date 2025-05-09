from io import StringIO
from typing import Generator
from html import escape
from xml.etree.ElementTree import Element

from .tag import Tag, TagKind
from .parser import parse_tags
from .transform import element_to_tag

# why implement XML encoding?
# https://github.com/oomol-lab/pdf-craft/issues/149
def encode(element: Element) -> str:
  buffer = StringIO()
  for cell in _split_cells(element):
    buffer.write(cell)
  return buffer.getvalue()

def _split_cells(element: Element) -> Generator[str, None, None]:
  if len(element) == 0 and not element.text:
    tag = element_to_tag(element, TagKind.SELF_CLOSING)
    yield str(tag)
  else:
    opening_tag = element_to_tag(element, TagKind.OPENING)
    closing_tag = element_to_tag(element, TagKind.CLOSING)
    yield str(opening_tag)
    if element.text:
      yield from _escape_text(element.text)
    for child in element:
      yield from _split_cells(child)
      if child.tail:
        yield from _escape_text(child.tail)
    yield str(closing_tag)

def _escape_text(text: str) -> Generator[str, None, None]:
  for cell in parse_tags(text):
    if isinstance(cell, Tag):
      cell = escape(str(cell))
    yield cell