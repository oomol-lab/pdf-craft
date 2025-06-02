from pathlib import Path
from typing import TypedDict
from strenum import StrEnum
from abc import ABC, abstractmethod


class Phase(StrEnum):
  Text = "text"
  FOOTNOTE = "footnote"
  GENERATION = "generation"
  COMPLETED = "completed"

class Level(StrEnum):
  Single = "single"
  Multiple = "multiple"

class State(TypedDict):
  phase: Phase
  level: Level
  max_data_tokens: int
  completed_ranges: list[list[int]]

class Corrector(ABC):
  @abstractmethod
  def do(self, from_path: Path, request_path: Path, is_footnote: bool) -> None:
    raise NotImplementedError()