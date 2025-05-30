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

  def progress(self, completed_count: int, max_count: int):
    self._call_report_progress(completed_count, max_count)
    self._progress = completed_count
    self._max_progress_count = max_count

  def set(self, max_count: int):
    self._max_progress_count = max_count

  def increment(self, count: int = 1):
    self._progress += count
    self._call_report_progress(self._progress, self._max_progress_count)

  def _call_report_progress(self, completed_count: int, max_count: int | None) -> None:
    if self._report_progress is None:
      return
    if max_count is not None and completed_count > max_count:
      return
    self._report_progress(completed_count, max_count)