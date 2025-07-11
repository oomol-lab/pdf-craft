from pathlib import Path

from ...llm import LLM
from ..reporter import Reporter, AnalysingStep
from ..utils import Context, MultiThreads
from .common import Phase, State, SequenceType
from .ocr_extractor import extract_ocr
from .joint import join


def extract_sequences(
      llm: LLM,
      reporter: Reporter,
      threads: MultiThreads,
      workspace_path: Path,
      ocr_path: Path,
      max_request_data_tokens: int,
      max_verify_paragraph_tokens: int,
      max_verify_paragraphs_count: int,
    ) -> None:

  context: Context[State] = Context(
    reporter=reporter,
    path=workspace_path,
    init=lambda: {
      "phase": Phase.EXTRACTION.value,
      "max_request_data_tokens": max_request_data_tokens,
      "max_verify_paragraph_tokens": max_verify_paragraph_tokens,
      "max_verify_paragraphs_count": max_verify_paragraphs_count,
      "completed_ranges": [],
    },
  )
  while context.state["phase"] != Phase.COMPLETED:
    if context.state["phase"] == Phase.EXTRACTION:
      reporter.go_to_step(AnalysingStep.EXTRACT_SEQUENCE)
      extract_ocr(
        llm=llm,
        context=context,
        threads=threads,
        ocr_path=ocr_path,
      )
      context.state = {
        **context.state,
        "phase": Phase.TEXT_JOINT.value,
        "completed_ranges": [],
      }
    elif context.state["phase"] == Phase.TEXT_JOINT:
      reporter.go_to_step(AnalysingStep.VERIFY_TEXT_PARAGRAPH)
      join(
        llm=llm,
        context=context,
        threads=threads,
        type=SequenceType.TEXT,
        extraction_path=workspace_path / Phase.EXTRACTION.value,
        join_path=workspace_path / Phase.TEXT_JOINT.value,
      )
      context.state = {
        **context.state,
        "phase": Phase.FOOTNOTE_JOINT.value,
        "completed_ranges": [],
      }
    elif context.state["phase"] == Phase.FOOTNOTE_JOINT:
      reporter.go_to_step(AnalysingStep.VERIFY_FOOTNOTE_PARAGRAPH)
      join(
        llm=llm,
        context=context,
        threads=threads,
        type=SequenceType.FOOTNOTE,
        extraction_path=workspace_path / Phase.EXTRACTION.value,
        join_path=workspace_path / Phase.FOOTNOTE_JOINT.value,
      )
      context.state = {
        **context.state,
        "phase": Phase.COMPLETED.value,
        "completed_ranges": [],
      }