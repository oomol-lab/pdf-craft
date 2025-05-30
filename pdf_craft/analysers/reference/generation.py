from pathlib import Path
from typing import TypedDict
from strenum import StrEnum

from ..reporter import Reporter, AnalysingStep
from ..utils import Context
from .footnote import append_footnote_for_chapters, generate_footnote_references


class _Phase(StrEnum):
  GENERATE_FOOTNOTES = "generate_footnotes"
  APPEND_FOOTNOTES = "append_footnotes"
  COMPLETED = "completed"

class _State(TypedDict):
  phase: _Phase

def generate_chapters_with_footnotes(
      reporter: Reporter,
      chapter_path: Path,
      footnote_sequence_path: Path,
      workspace_path: Path,
    ) -> Path:

  output_path = workspace_path / "output"
  context: Context[_State] = Context(
    reporter=reporter,
    path=workspace_path,
    init=lambda: {
      "phase": _Phase.GENERATE_FOOTNOTES.value,
    },
  )
  if context.state["phase"] == _Phase.GENERATE_FOOTNOTES:
    reporter.go_to_step(AnalysingStep.GENERATE_FOOTNOTES)
    generate_footnote_references(
      sequence_path=footnote_sequence_path,
      output_path=context.path,
    )
    context.state = {
      **context.state,
      "phase": _Phase.APPEND_FOOTNOTES.value,
    }

  if context.state["phase"] == _Phase.APPEND_FOOTNOTES:
    append_footnote_for_chapters(
      chapter_path=chapter_path,
      footnote_path=context.path,
      output_path=output_path,
    )
    context.state = {
      **context.state,
      "phase": _Phase.COMPLETED.value,
    }
  return output_path
