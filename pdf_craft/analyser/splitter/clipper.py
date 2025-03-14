from typing import Generator, Callable
from dataclasses import dataclass
from xml.etree.ElementTree import tostring, Element
from .segment import Segment
from .group import Group
from ..common import TextInfo
from ..llm import LLM


@dataclass
class PageXML:
  page_index: int
  xml: Element
  is_gap: bool = False

def get_and_clip_pages(llm: LLM, group: Group, get_element: Callable[[int], Element]) -> list[PageXML]:
  head = _get_pages(
    llm=llm,
    items=group.head,
    remain_tokens=group.head_remain_tokens,
    clip_tail=False,
    get_element=get_element,
  )
  tail = _get_pages(
    llm=llm,
    items=group.tail,
    remain_tokens=group.tail_remain_tokens,
    clip_tail=True,
    get_element=get_element,
  )
  body = _get_pages(
    llm=llm,
    items=group.body,
    remain_tokens=None,
    clip_tail=True,
    get_element=get_element,
  )
  page_xml_list: list[PageXML] = []

  for page_xml in reversed(list(head)):
    page_xml.is_gap = True
    page_xml_list.append(page_xml)

  for page_xml in body:
    page_xml_list.append(page_xml)

  for page_xml in tail:
    page_xml.is_gap = True
    page_xml_list.append(page_xml)

  return page_xml_list

def _get_pages(
    llm: LLM,
    items: list[TextInfo | Segment],
    remain_tokens: int | None,
    clip_tail: bool,
    get_element: Callable[[int], str],
  ) -> Generator[PageXML, None, None]:

  if len(items) == 0:
    return

  if remain_tokens is not None:
    tokens = sum(item.tokens for item in items)
    if remain_tokens == tokens:
      remain_tokens = None

  if remain_tokens is not None:
    assert len(items) == 1

  if remain_tokens is None:
    for item in items:
      if isinstance(item, TextInfo):
        yield PageXML(
          page_index=item.page_index,
          xml=get_element(item.page_index),
        )
      elif isinstance(item, Segment):
        for text_info in item.text_infos:
          yield PageXML(
            page_index=text_info.page_index,
            xml=get_element(text_info.page_index),
          )
  else:
    item = items[0]
    if isinstance(item, TextInfo):
      page_xml = get_element(item.page_index)
      page_xml = _clip_element(llm, page_xml, remain_tokens, clip_tail)
      if page_xml is not None:
        yield PageXML(
          page_index=item.page_index,
          xml=page_xml,
        )

    elif isinstance(item, Segment):
      text_infos, remain_tokens = _clip_segment(item, remain_tokens, clip_tail)
      page_xml_list: list[PageXML] = []
      for i, text_info in enumerate(text_infos):
        page_xml: Element | None = get_element(text_info.page_index)
        if (clip_tail and i == len(text_infos) - 1) or \
           (not clip_tail and i == 0):
          page_xml = _clip_element(llm, page_xml, remain_tokens, clip_tail)
        if page_xml is not None:
          page_xml_list.append(PageXML(
            page_index=text_info.page_index,
            xml=page_xml,
          ))
      if not clip_tail:
        page_xml_list.reverse()
      yield from page_xml_list

def _clip_segment(segment: Segment, remain_tokens: int, clip_tail: bool):
  clipped: list[TextInfo] = []
  iter_source = segment.text_infos
  if not clip_tail:
    iter_source = reversed(iter_source)

  for text_info in iter_source:
    clipped.append(text_info)
    if remain_tokens >= text_info.tokens:
      remain_tokens -= text_info.tokens
    else:
      break
  if not clip_tail:
    clipped.reverse()

  return clipped, remain_tokens

def _clip_element(llm: LLM, element: Element, remain_tokens: int, clip_tail: bool) -> Element | None:
  clipped_element = Element(element.tag, element.attrib)
  children: list[tuple[Element, int]] = []
  remain_tokens -= llm.count_tokens_count(tostring(clipped_element, encoding="unicode"))

  for child in element:
    child_text = tostring(child, encoding="unicode")
    child_tokens = llm.count_tokens_count(child_text)
    children.append((child, child_tokens))
  if not clip_tail:
    children.reverse()

  if len(children) == 0:
    text = element.text
    if text is None:
      return None
    tokens = llm.encode_tokens(text)
    if clip_tail:
      tokens = tokens[:remain_tokens]
    else:
      tokens = tokens[len(tokens) - remain_tokens:]
    if len(tokens) == 0:
      return None
    clipped_element.text = llm.decode_tokens(tokens)

  else:
    clipped_children: list[Element] = []
    for child, tokens_count in children:
      if remain_tokens >= tokens_count:
        remain_tokens -= tokens_count
        clipped_children.append(child)
      else:
        child = _clip_element(llm, child, remain_tokens, clip_tail)
        if child is not None:
          clipped_children.append(child)
        break
    if len(clipped_children) == 0:
      return None
    if not clip_tail:
      clipped_children.reverse()
    clipped_element.extend(clipped_children)

  return clipped_element