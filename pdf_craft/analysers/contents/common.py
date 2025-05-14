from typing import TypedDict
from strenum import StrEnum


class Phase(StrEnum):
  INIT = "init"
  COLLECT = "collect"
  ANALYSE = "analyse"
  COMPLETED = "completed"

class State(TypedDict):
  phase: Phase
  max_data_tokens: int
  page_indexes: list[int]