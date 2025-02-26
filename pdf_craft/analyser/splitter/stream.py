from typing import Iterable, Iterator, TypeVar, Generic


E = TypeVar("E")

class Stream(Generic[E]):
  def __init__(self, elements: Iterable[E]):
    self._iterator: Iterator[E] = iter(elements)
    self._buffer: list[E] = []

  @property
  def has_buffer(self) -> bool:
    return len(self._buffer) > 0

  def recover(self, element: E):
    self._buffer.append(element)

  def get(self) -> E | None:
    if len(self._buffer) > 0:
      return self._buffer.pop()
    else:
      return next(self._iterator, None)