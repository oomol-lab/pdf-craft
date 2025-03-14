from typing import Callable
from enum import auto, Enum

class AnalysingStep(Enum):
  OCR = auto()
  ANALYSE_PAGE = auto()
  EXTRACT_INDEX = auto()
  EXTRACT_CITATION = auto()
  EXTRACT_MAIN_TEXT = auto()
  MARK_POSITION = auto()
  ANALYSE_META = auto()
  GENERATE_CHAPTERS = auto()

# func(completed_count: int) -> None
AnalysingProgressReport = Callable[[int], None]

# func(step: AnalysingStep, count: int) -> None
AnalysingStepReport = Callable[[AnalysingStep, int], None]
