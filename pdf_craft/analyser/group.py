from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from math import floor
from .secondary import TextInfo
from .segment import Segment
from .utils import Stream


_Item = TextInfo | Segment

@dataclass
class Group:
  pre_count: int
  next_count: int
  items: list[_Item]

class _Group:
  def __init__(self, max_tokens: int, gap_max_tokens: float):
    self._max_tokens: int = max_tokens
    self._gap_max_tokens: int = gap_max_tokens
    body_max_tokens = max_tokens - gap_max_tokens * 2
    assert body_max_tokens > 0

    # head and tail are passed to LLM as additional text
    # to let LLM understand the context of the body text.
    self.head: _Buffer = _Buffer(gap_max_tokens)
    self.tail: _Buffer = _Buffer(gap_max_tokens)
    self.body: _Buffer = _Buffer(body_max_tokens)

  @property
  def body_has_any(self) -> bool:
    return self.body.has_any

  def seal_head(self):
    self.head.seal()

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
    next_group: _Group = _Group(self._max_tokens, self._gap_max_tokens)
    next_head = next_group.head
    for item in reversed([*self.head, *self.body]):
      if next_head.can_append(item):
        next_head.append(item)
      else:
        next_head.reverse().seal()
        break
    return next_group

  def report(self) -> Group:
    raise NotImplementedError("TODO:")

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

def group(items: Iterable[TextInfo | Segment], max_tokens: int, gap_rate: float):
  curr_group: _Group | None = None
  stream: Stream[_Item] = Stream(items)
  while True:
    item = stream.get()
    if item is None:
      break
    if curr_group is None:
      gap_max_tokens = floor(max_tokens * gap_rate)
      assert gap_max_tokens > 0
      curr_group = _Group(max_tokens, gap_max_tokens)
      curr_group.seal_head()

    success = curr_group.append(item)
    if not success:
      if curr_group.body_has_any:
        yield curr_group.report()
      stream.recover(item)
      for item in reversed(list(curr_group.tail)):
        stream.recover(item)
      curr_group = curr_group.next()

  if curr_group is not None and \
     curr_group.body_has_any:
    yield curr_group.report()