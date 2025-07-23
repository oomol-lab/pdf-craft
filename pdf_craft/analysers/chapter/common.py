from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  MAPPER = "mapper"
  CHAPTER = "chapter"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  has_contents: bool
  completed_ranges: list[list[int]]
  max_request_tokens: int
