from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Generator
from math import floor
from .segment import Segment
from .stream import Stream
from ..common import TextInfo


@dataclass
class Group:
  head_remain_tokens: int
  tail_remain_tokens: int
  head: list[TextInfo | Segment]
  body: list[TextInfo | Segment]
  tail: list[TextInfo | Segment]

def group(
    items: Iterable[TextInfo | Segment],
    max_tokens: int,
    gap_rate: float,
    tail_rate: float,
  ) -> Generator[Group, None, None]:

  gap_max_tokens = floor(max_tokens * gap_rate)
  assert gap_max_tokens > 0

  curr_group: _Group = _Group(_Attributes(
    max_tokens=max_tokens,
    gap_max_tokens=gap_max_tokens,
    tail_rate=tail_rate,
  ))
  curr_group.head.seal()
  stream: Stream[_Item] = Stream(items)

  while True:
    item = stream.get()
    if item is not None:
      success = curr_group.append(item)
      if success:
        continue

    if curr_group.body.has_any:
      yield curr_group.report()
    if item is not None:
      stream.recover(item)
    for tail_item in reversed(list(curr_group.tail)):
      stream.recover(tail_item)

    if not stream.has_buffer and item is None:
      # next item never comes
      break
    curr_group = curr_group.next()

_Item = TextInfo | Segment

@dataclass
class _Attributes:
  max_tokens: int
  gap_max_tokens: float
  tail_rate: float

class _Group:
  def __init__(self, attr: _Attributes):
    self._attr: _Attributes = attr
    body_max_tokens = attr.max_tokens - attr.gap_max_tokens * 2
    assert body_max_tokens > 0

    # head and tail are passed to LLM as additional text
    # to let LLM understand the context of the body text.
    self.head: _Buffer = _Buffer(attr.gap_max_tokens)
    self.tail: _Buffer = _Buffer(attr.gap_max_tokens)
    self.body: _Buffer = _Buffer(body_max_tokens)

  def append(self, item: _Item) -> bool:
    success: bool = False
    for buffer in (self.head, self.body, self.tail):
      if buffer.is_sealed:
        continue
      if not buffer.can_append(item):
        buffer.seal()
        continue
      buffer.append(item)
      success = True
      break
    return success

  def next(self) -> _Group:
    next_group: _Group = _Group(self._attr)
    next_head = next_group.head
    for item in reversed([*self.head, *self.body]):
      if next_head.can_append(item):
        next_head.append(item)
      else:
        next_head.reverse().seal()
        break
    return next_group

  def report(self) -> Group:
    tokens: int = 0
    for buffer in (self.head, self.body, self.tail):
      tokens += buffer.tokens

    head_remain_tokens = self.head.tokens
    tail_remain_tokens = self.tail.tokens

    if tokens > self._attr.max_tokens:
      if self.body.tokens > self._attr.max_tokens:
        head_remain_tokens = 0
        tail_remain_tokens = 0
      else:
        tail_rate = self._attr.tail_rate
        remain_tokens = self._attr.max_tokens - self.body.tokens
        if self.head.tokens < remain_tokens * (1.0 - tail_rate):
          tail_remain_tokens = remain_tokens - self.head.tokens
        elif self.tail.tokens < remain_tokens * tail_rate:
          head_remain_tokens = remain_tokens - self.tail.tokens
        else:
          head_remain_tokens = round(remain_tokens * (1.0 - tail_rate))
          tail_remain_tokens = round(remain_tokens * tail_rate)

    head = list(self.head)
    tail = list(self.tail)

    if head_remain_tokens == 0:
      head = []
    if tail_remain_tokens == 0:
      tail = []

    return Group(
      head_remain_tokens=head_remain_tokens,
      tail_remain_tokens=tail_remain_tokens,
      head=head,
      body=list(self.body),
      tail=tail,
    )

class _Buffer:
  def __init__(self, max_tokens: int):
    self._max_tokens: int = max_tokens
    self._items: list[_Item] = []
    self._tokens: int = 0
    self._is_sealed: bool = False

  @property
  def is_sealed(self) -> bool:
    return self._is_sealed

  @property
  def has_any(self) -> bool:
    return len(self._items) > 0

  @property
  def tokens(self) -> int:
    return self._tokens

  def seal(self):
    self._is_sealed = True

  def reverse(self) -> _Buffer:
    self._items.reverse()
    return self

  def __iter__(self):
    return iter(self._items)

  def append(self, item: _Item):
    self._items.append(item)
    self._tokens += item.tokens

  def can_append(self, item: _Item) -> bool:
    if self._is_sealed:
      return False
    if len(self._items) == 0:
      return True
    next_tokens = self._tokens + item.tokens
    return next_tokens <= self._max_tokens