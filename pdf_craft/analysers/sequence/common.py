from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  EXTRACTION = "extraction"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  max_data_tokens: int
  completed_ranges: list[list[int]]

class Truncation(StrEnum):
  MUST_BE = "must-be"
  MOST_LIKELY = "most-likely"
  IMPOSSIBLE = "impossible"
  UNCERTAIN = "uncertain"