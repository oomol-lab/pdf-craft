from dataclasses import dataclass
from typing import Iterator


@dataclass
class RangeNotIncluded:
  pass

@dataclass
class RangeMatched:
  pass

@dataclass
class RangeOverlapped:
  ranges: list[tuple[int, int]]

class RangeState:
  def __init__(self, json_state: list[list[int]]):
    self._ranges: list[tuple[int, int]] = []
    for cell in json_state:
      if len(cell) != 2:
        continue
      begin, end = cell
      begin = self._2int(begin)
      end = self._2int(end)
      if begin is None or end is None:
        continue
      self._ranges.append((begin, end))

  def _2int(self, value: any) -> int | None:
    if isinstance(value, int):
      return value
    elif isinstance(value, float):
      return int(value)
    else:
      return None

  def to_json_state(self) -> list[list[int]]:
    return [[begin, end] for begin, end in self._ranges]

  def __iter__(self) -> Iterator[tuple[int, int]]:
    return iter(self._ranges)

  def check(self, begin: int, end: int) -> RangeMatched | RangeOverlapped | RangeNotIncluded:
    overlapped: list[tuple[int, int]] = []
    for r_begin, r_end in self._ranges:
      if begin == r_begin and end == r_end:
        return RangeMatched()
      elif begin <= r_end and end >= r_begin:
        overlapped.append((r_begin, r_end))
    if overlapped:
      return RangeOverlapped(overlapped)
    else:
      return RangeNotIncluded()

  def add(self, begin: int, end: int) -> None:
    assert begin <= end
    self._ranges.append((begin, end))
    self._ranges.sort()