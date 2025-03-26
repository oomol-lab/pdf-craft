from typing import Generator, Callable, Iterable
from dataclasses import dataclass
from xml.etree.ElementTree import tostring, Element
from resource_segmentation import Group, Segment, Resource
from ..llm import LLM
from .common import PageRef


@dataclass
class PageXML:
  page_index: int
  xml: Element
  is_gap: bool = False

def get_and_clip_pages(llm: LLM, group: Group[PageRef], get_element: Callable[[int], Element]) -> list[PageXML]:
  head = _get_pages(
    llm=llm,
    items=group.head,
    remain_tokens=group.head_remain_count,
    clip_tail=False,
    get_element=get_element,
  )
  tail = _get_pages(
    llm=llm,
    items=group.tail,
    remain_tokens=group.tail_remain_count,
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
    items: list[Resource[PageRef] | Segment[PageRef]],
    remain_tokens: int | None,
    clip_tail: bool,
    get_element: Callable[[int], Element],
  ) -> Generator[PageXML, None, None]:

  if len(items) == 0:
    return

  if remain_tokens is not None:
    tokens = sum(item.count for item in items)
    if remain_tokens == tokens:
      remain_tokens = None

  if remain_tokens is None:
    for text_info in _search_resources(items):
      page_index = text_info.payload.page_index
      yield PageXML(
        page_index=page_index,
        xml=get_element(page_index),
      )
  else:
    clipped: list[Resource[PageRef]] = []
    page_xml_list: list[PageXML] = []
    iter_source: Iterable[Resource[PageRef]] = _search_resources(items)
    if not clip_tail:
      iter_source = reversed(list(iter_source))

    for text_info in iter_source:
      if remain_tokens > 0:
        clipped.append(text_info)
      if remain_tokens >= text_info.count:
        remain_tokens -= text_info.count
      else:
        break
    if not clip_tail:
      clipped.reverse()

    for i, resource in enumerate(clipped):
      page_index = resource.payload.page_index
      page_xml: Element | None = get_element(page_index)

      if remain_tokens > 0 and (
        (clip_tail and i == len(clipped) - 1) or \
        (not clip_tail and i == 0)
      ):
        page_xml = _clip_element(
          llm=llm,
          element=page_xml,
          remain_tokens=remain_tokens,
          clip_tail=clip_tail,
        )
      if page_xml is not None:
        page_xml_list.append(PageXML(
          page_index=page_index,
          xml=page_xml,
        ))

    if not clip_tail:
      page_xml_list.reverse()
    yield from page_xml_list

def _search_resources(items: list[Resource[PageRef] | Segment[PageRef]]):
  for item in items:
    if isinstance(item, Resource):
      yield item
    elif isinstance(item, Segment):
      yield from item.resources

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