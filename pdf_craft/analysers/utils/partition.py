from __future__ import annotations
from threading import Lock
from typing import Any, TypeVar, Generic, Sequence, Iterator, Callable, Generator
from .context import Context


T = TypeVar("T", bound=tuple[int, ...])
S = TypeVar("S", bound=dict[str, Any])
P = TypeVar("P")

_STATE_KEY = "completed_ranges"

# thread safe
class Partition(Generic[T, S, P]):
  def __init__(
        self,
        dimension: int,
        context: Context[S],
        sequence: Sequence[tuple[T, T, P]],
        remove: Callable[[T, T], None] | None = None,
        done: Callable[[T, T], None] | None = None,
      ) -> None:

    super().__init__()
    self._dimension: int = dimension
    self._context: Context[S] = context
    self._range_iterator: Iterator[tuple[T | int, T | int, P]] = iter(sequence)
    self._done: Callable[[T, T], None] = done or (lambda _, __: None)
    self._remove: Callable[[T, T], None] = remove or (lambda _, __: None)
    self._iter_lock: Lock = Lock()
    self._range_lock: Lock = Lock()
    self._last_index: T | None = None
    self._done_ranges: list[tuple[T, T]] = []
    self._to_remove_ranges: list[tuple[T, T]] = []

    for item in context.state.get(_STATE_KEY, ()):
      half_len = len(item) // 2
      begin: list[int] = []
      end: list[int] = []
      for i in range(half_len):
        begin.append(item[i])
        end.append(item[i + half_len])
      self._done_ranges.append((
        tuple(begin),
        tuple(end),
      ))

    self._done_ranges.sort(key=lambda x: x[0])

  def __enter__(self) -> Partition[T, S, P]:
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    with self._range_lock:
      self._done_ranges.sort(key=lambda x: x[0])
      if exc_type is None and self._last_index is not None:
        last_index: int = -1
        for i, (begin, end) in enumerate(self._done_ranges):
          if self._last_index < max(begin, end):
            last_index = i
            break
        if last_index >= 0:
          self._done_ranges = self._done_ranges[:last_index]
          self._sync_done_ranges()

      for begin, end in self._to_remove_ranges:
        self._remove(begin, end)
      self._to_remove_ranges.clear()

    return False

  def pop_tasks(self) -> Generator[PartitionTask[T, S, P], None, None]:
    while True:
      task = self.pop_task()
      if task is None:
        break
      yield task

  def pop_task(self) -> PartitionTask[T, S, P] | None:
    with self._iter_lock:
      while True:
        range = next(self._range_iterator, None)
        if range is None:
          return None

        begin, end, payload = range
        begin = self._assert_index(begin)
        end = self._assert_index(end)

        if (begin, end) in self._done_ranges:
          self._done(begin, end)
          continue

        with self._range_lock:
          if self._last_index is None:
            self._last_index = max(begin, end)
          else:
            self._last_index = max(begin, end, self._last_index)

        return PartitionTask(
          begin=begin,
          end=end,
          payload=payload,
          done=lambda: self._on_task_done(begin, end),
        )

  def _assert_index(self, index: T | int) -> T:
    if isinstance(index, int):
      index = (index,)
    else:
      assert isinstance(index, tuple), f"Index must be a tuple, got {type(index)}"

    assert len(index) == self._dimension, f"Index must have length {self._dimension}, got {len(index)}"
    for i, x in enumerate(index):
      assert isinstance(x, int), f"Index[{i}] must be an int, got {type(x)}"

    return index

  def _on_task_done(self, done_begin: T, done_end: T) -> None:
    with self._range_lock:
      found_matched = False
      new_done_ranges: list[tuple[T, T]] = []
      for begin, end in self._done_ranges:
        if done_begin <= end and done_end >= begin:
          self._to_remove_ranges.append((begin, end))
        else:
          if done_begin == begin and done_end == end:
            found_matched = True
          new_done_ranges.append((begin, end))

      if not found_matched:
        new_done_ranges.append((done_begin, done_end))

      new_done_ranges.sort(key=lambda x: x[0])
      self._done_ranges = new_done_ranges
      self._sync_done_ranges()
      self._done(done_begin, done_end)

  def _sync_done_ranges(self) -> None:
    ranges: list[list[int]] = []
    self._done_ranges.sort(key=lambda x: x[0])
    for begin, end in self._done_ranges:
      ranges.append([*begin, *end])
    self._context.state = {
      **self._context.state,
      _STATE_KEY: ranges,
    }

class PartitionTask(Generic[T, S, P]):
  def __init__(self, begin: T, end: T, payload: P, done: Callable[[], None]) -> None:
    super().__init__()
    self.begin: T = begin
    self.end: T = end
    self.payload: P = payload
    self._done: Callable[[], None] = done

  def __enter__(self) -> PartitionTask[T, S, P]:
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_type is None:
      self._done()
    return False
