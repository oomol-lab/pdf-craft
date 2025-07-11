from typing import Callable
from enum import auto, Enum
from threading import Lock


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

# thread safe
class Reporter:
  def __init__(
      self,
      report_step: AnalysingStepReport | None,
      report_progress: AnalysingProgressReport | None,
    ) -> None:

    self._lock: Lock = Lock()
    self._report_step: AnalysingStepReport | None = report_step
    self._report_progress: AnalysingProgressReport | None = report_progress
    self._progress: int = 0
    self._max_progress_count: int | None = None

  def go_to_step(self, step: AnalysingStep) -> None:
    with self._lock:
      if self._report_step is not None:
        self._report_step(step)
      self._progress = 0
      self._max_progress_count = None

  def set(self, max_count: int) -> None:
    with self._lock:
      self._max_progress_count = max_count

  def set_progress(self, progress: int) -> None:
    with self._lock:
      if self._max_progress_count is not None:
        progress = min(progress, self._max_progress_count)

      if progress == self._progress:
        return
      self._progress = progress
      if self._report_progress is None:
        return

      self._report_progress(self._progress, self._max_progress_count)

  def increment(self, count: int = 1) -> None:
    self.set_progress(self._progress + count)

