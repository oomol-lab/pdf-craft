from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Generator
from .stream import Stream
from ..common import TextIncision, TextInfo


_MIN_LEVEL = -1

@dataclass
class Segment:
  tokens: int
  text_infos: list[TextInfo]

def allocate_segments(text_infos: Iterable[TextInfo], max_tokens: int) -> Generator[TextInfo | Segment, None, None]:
  segment = _collect_segment(Stream(text_infos), _MIN_LEVEL)
  for item in segment.children:
    if isinstance(item, _Segment):
      for segment in _split_segment_if_need(item, max_tokens):
        yield _transform_segment(segment)
    elif isinstance(item, TextInfo):
      yield item

def _transform_segment(segment: _Segment):
  children = list(_deep_iter_segment(segment))
  if len(children) == 1:
    return children[0]
  else:
    return Segment(
      tokens=segment.tokens,
      text_infos=children,
    )

@dataclass
class _Segment:
  level: int
  tokens: int
  start_incision: TextIncision
  end_incision: TextIncision
  children: list[TextInfo | _Segment]

def _collect_segment(stream: Stream[TextInfo], level: int) -> _Segment:
  start_incision: TextIncision = TextIncision.IMPOSSIBLE
  end_incision: TextIncision = TextIncision.IMPOSSIBLE
  children: list[TextInfo | _Segment] = []

  while True:
    text = stream.get()
    if text is None:
      break
    if len(children) == 0: # is the first
      start_incision = text.start_incision
      children.append(text)
    else:
      pre_text = children[-1]
      incision_level = _to_level(
        pre_text.end_incision,
        text.start_incision,
      )
      if incision_level < level:
        stream.recover(text)
        end_incision = text.end_incision
        break
      elif incision_level > level:
        stream.recover(text)
        stream.recover(pre_text)
        segment = _collect_segment(stream, incision_level)
        children[-1] = segment
      else:
        children.append(text)

  tokens: int = 0
  for child in children:
    tokens += child.tokens

  return _Segment(
    level=level,
    tokens=tokens,
    start_incision=start_incision,
    end_incision=end_incision,
    children=children,
  )

def _split_segment_if_need(segment: _Segment, max_tokens: int):
  if segment.tokens <= max_tokens:
    yield segment
  else:
    tokens: int = 0
    children: list[TextInfo | _Segment] = []

    for item in _unfold_segments(segment, max_tokens):
      if len(children) > 0 and tokens + item.tokens > max_tokens:
        yield _create_segment(tokens, children, segment.level)
        tokens = 0
        children = []
      tokens += item.tokens
      children.append(item)

    if len(children) > 0:
      yield _create_segment(tokens, children, segment.level)

def _unfold_segments(segment: _Segment, max_tokens: int) -> Generator[TextInfo | _Segment]:
  for item in segment.children:
    if item.tokens > max_tokens and isinstance(item, _Segment):
      for sub_item in _split_segment_if_need(item, max_tokens):
        yield sub_item
    else:
      yield item

def _create_segment(tokens: int, children: list[TextInfo | _Segment], level: int) -> _Segment:
  return _Segment(
    level=level,
    tokens=tokens,
    children=children,
    start_incision=children[0].start_incision,
    end_incision=children[-1].end_incision,
  )

def _deep_iter_segment(segment: _Segment) -> Generator[TextInfo, None, None]:
  for child in segment.children:
    if isinstance(child, _Segment):
      yield from _deep_iter_segment(child)
    elif isinstance(child, TextInfo):
      yield child

def _to_level(left_incision: TextIncision, right_incision: TextIncision) -> int:
  return max(_MIN_LEVEL, left_incision.value + right_incision.value)
