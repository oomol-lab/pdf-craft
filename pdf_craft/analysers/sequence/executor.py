from pathlib import Path

from ...llm import LLM
from ..context import Context
from .common import Phase, State
from .ocr_extractor import extract_ocr


def extract_sequences(llm: LLM, workspace: Path, ocr_path: Path, max_data_tokens: int) -> None:
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.EXTRACTION.value,
    "max_data_tokens": max_data_tokens,
    "completed_ranges": [],
  })
  while context.state["phase"] != Phase.COMPLETED:
    if context.state["phase"] == Phase.EXTRACTION:
      extract_ocr(
        llm=llm,
        context=context,
        ocr_path=ocr_path,
      )
      context.state = {
        **context.state,
        "phase": Phase.COMPLETED.value,
      }