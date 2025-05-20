from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  Text = "text"
  FOOTNOTE = "footnote"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  max_data_tokens: int
  completed_ranges: list[list[int]]