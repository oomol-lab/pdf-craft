from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  MAPPER = "mapper"
  CHAPTER = "chapter"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  completed_ranges: list[list[int]]
