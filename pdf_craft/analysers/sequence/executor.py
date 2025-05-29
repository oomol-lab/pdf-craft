from pathlib import Path

from ...llm import LLM
from ..utils import Context
from .common import Phase, State, SequenceType
from .ocr_extractor import extract_ocr
from .joint import join


def extract_sequences(llm: LLM, workspace: Path, ocr_path: Path, max_data_tokens: int) -> None:
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.EXTRACTION.value,
    "max_data_tokens": max_data_tokens,
    "max_paragraph_tokens": 512,
    "max_paragraphs": 8,
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
        "phase": Phase.TEXT_JOINT.value,
        "completed_ranges": [],
      }
    elif context.state["phase"] == Phase.TEXT_JOINT:
      join(
        llm=llm,
        context=context,
        type=SequenceType.TEXT,
        extraction_path=workspace / Phase.EXTRACTION.value,
        join_path=workspace / Phase.TEXT_JOINT.value,
      )
      context.state = {
        **context.state,
        "phase": Phase.FOOTNOTE_JOINT.value,
        "completed_ranges": [],
      }
    elif context.state["phase"] == Phase.FOOTNOTE_JOINT:
      join(
        llm=llm,
        context=context,
        type=SequenceType.FOOTNOTE,
        extraction_path=workspace / Phase.EXTRACTION.value,
        join_path=workspace / Phase.FOOTNOTE_JOINT.value,
      )
      context.state = {
        **context.state,
        "phase": Phase.COMPLETED.value,
        "completed_ranges": [],
      }