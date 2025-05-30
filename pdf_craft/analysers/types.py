from dataclasses import dataclass
from typing import Callable
from enum import auto, Enum


class AnalysingStep(Enum):
  OCR = auto()
  EXTRACT_SEQUENCE = auto()
  VERIFY_TEXT_PARAGRAPH = auto()
  VERIFY_FOOTNOTE_PARAGRAPH = auto()
  CORRECT_TEXT = auto()
  CORRECT_FOOTNOTE = auto()
  EXTRACT_META = auto()
  COLLECT_CONTENTS = auto()
  ANALYSE_CONTENTS = auto()
  MAPPING_CONTENTS = auto()
  GENERATE_FOOTNOTES = auto()
  OUTPUT = auto()

# func(completed_count: int, max_count: int | None) -> None
AnalysingProgressReport = Callable[[int, int | None], None]

# func(step: AnalysingStep) -> None
AnalysingStepReport = Callable[[AnalysingStep], None]

@dataclass
class LLMWindowTokens:
  main_texts: int | None = None
  citations: int | None = None