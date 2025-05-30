from .types import (
  AnalysingStep,
  AnalysingStepReport,
  AnalysingProgressReport,
)


class Reporter:
  def __init__(
      self,
      report_step: AnalysingStepReport | None,
      report_progress: AnalysingProgressReport | None,
    ) -> None:
    self._report_step: AnalysingStepReport | None = report_step
    self._report_progress: AnalysingProgressReport | None = report_progress
    self._progress: int = 0
    self._max_progress_count: int | None = None

  def go_to_step(self, step: AnalysingStep):
    if self._report_step is not None:
      self._report_step(step)
    self._progress = 0
    self._max_progress_count = None

  def set(self, max_count: int):
    self._max_progress_count = max_count

  def increment(self, count: int = 1):
    next_progress = self._progress + count
    if self._max_progress_count is not None:
      next_progress = min(next_progress, self._max_progress_count)

    if next_progress == self._progress:
      return
    self._progress = next_progress
    if self._report_progress is None:
      return

    self._report_progress(next_progress, self._max_progress_count)