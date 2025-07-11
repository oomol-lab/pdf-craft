from typing import Generator
from dataclasses import dataclass
from enum import IntEnum
from xml.etree.ElementTree import Element
from resource_segmentation import split, Resource, Segment, Group
from ..data import ParagraphType


class TruncationKind(IntEnum):
  NO = -1
  VERIFIED = 1
  UNCERTAIN = 0

@dataclass
class _Child:
  page_index: int
  element: Element
  tokens: int
  tail: TruncationKind

class ParagraphDraft:
  def __init__(self, type: ParagraphType):
    self._type: ParagraphType = type
    self._children: list[_Child] = []
    self._tokens: int = 0

  @property
  def page_index(self) -> int:
    return self._children[0].page_index

  @property
  def type(self) -> ParagraphType:
    return self._type

  @property
  def tokens(self) -> int:
    return self._tokens

  def append(self, page_index: int, element: Element, tokens: int):
    self._tokens += tokens
    self._children.append(_Child(
      page_index=page_index,
      element=element,
      tokens=tokens,
      tail=TruncationKind.NO,
    ))

  def set_tail_truncation(self, kind: TruncationKind):
    self._children[-1].tail = kind

  def fork(self, max_chunk_tokens: int) -> Generator["ParagraphDraft", None, None]:
    pre_incision: TruncationKind = TruncationKind.NO
    resources: list[Resource[_Child]] = []

    for child in self._children:
      incision = child.tail
      resources.append(Resource(
        count=child.tokens,
        start_incision=pre_incision,
        end_incision=incision,
        payload=child,
      ))
      pre_incision = incision

    for group in split(
      resources=iter(resources),
      max_segment_count=max_chunk_tokens,
      border_incision=TruncationKind.NO,
      gap_rate=0,
      tail_rate=0,
    ):
      children = [r.payload for r in self._iter_group_body(group)]
      if children:
        forked = ParagraphDraft(self._type)
        for child in children:
          forked.append(
            page_index=child.page_index,
            element=child.element,
            tokens=child.tokens,
          )
        forked.set_tail_truncation(children[-1].tail)
        yield forked

  def to_xml(self) -> Element:
    element = Element("paragraph")
    element.set("type", self._type.value)
    for child in self._children:
      element.append(child.element)
    return element

  def _iter_group_body(self, group: Group[_Child]):
    for item in group.body:
      if isinstance(item, Resource):
        yield item
      elif isinstance(item, Segment):
        for resource in item.resources:
          yield resource