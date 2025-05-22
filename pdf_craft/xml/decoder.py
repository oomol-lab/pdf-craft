from typing import Generator, Iterable
from xml.etree.ElementTree import Element

from .tag import Tag, TagKind
from .parser import parse_tags
from .transform import tag_to_element
from .utils import clone

# why implement XML decoding?
# https://github.com/oomol-lab/pdf-craft/issues/149
def decode_friendly(chars: Iterable[str], tags: Iterable[str] | str = ()) -> Generator[Element, None, None]:
  if isinstance(tags, str):
    tags = set((tags,))
  else:
    tags = set(tags)

  for element in _collect_elements(chars):
    if element.tag in tags or len(tags) == 0:
      yield clone(element)

def _collect_elements(chars: Iterable[str]) -> Generator[Element, None, None]:
  opening_stack: list[Element] = []
  last_closed_element: Element | None = None

  for cell in parse_tags(chars):
    if isinstance(cell, Tag):
      tag: Tag = cell
      element = tag_to_element(tag)
      if tag.kind == TagKind.CLOSING:
        popped = _pop_element(tag.name, opening_stack)
        if popped is not None:
          yield popped
          last_closed_element = popped
        elif last_closed_element is not None:
          _append_to_tail(last_closed_element, tag.proto)
      else:
        if opening_stack:
          opening_stack[-1].append(element)
        if tag.kind == TagKind.SELF_CLOSING:
          yield element
          last_closed_element = element
        elif tag.kind == TagKind.OPENING:
          opening_stack.append(element)
          last_closed_element = None

    elif last_closed_element is not None:
      _append_to_tail(last_closed_element, cell)

    elif opening_stack:
      opening_stack[-1].text = cell

def _append_to_tail(element: Element, text: str) -> None:
  if element.tail:
    element.tail += text
  else:
    element.tail = text

def _pop_element(tag_name: str, opening_stack: list[Element]) -> Element | None:
  index = -1
  for i in range(len(opening_stack) - 1, -1, -1):
    opening_element = opening_stack[i]
    if tag_name == opening_element.tag:
      index = i
      break
  if index == -1:
    return None

  popped: Element | None = None
  for _ in range(len(opening_stack) - index):
    popped = opening_stack.pop()
  return popped
