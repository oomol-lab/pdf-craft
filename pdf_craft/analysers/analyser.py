from os import PathLike
from pathlib import Path

from ..llm import LLM
from ..pdf import PDFPageExtractor

from .types import CorrectionMode
from .reporter import Reporter, AnalysingStep, AnalysingStepReport, AnalysingProgressReport
from .window import parse_window_tokens, LLMWindowTokens
from .ocr import generate_ocr_pages
from .sequence import extract_sequences
from .correction import correct, Level as CorrectionLevel
from .meta import extract_meta
from .contents import extract_contents
from .chapter import generate_chapters
from .reference import generate_chapters_with_footnotes
from .output import output
from .utils import MultiThreads


def analyse(
    llm: LLM,
    pdf_page_extractor: PDFPageExtractor,
    pdf_path: PathLike,
    analysing_dir_path: PathLike,
    output_dir_path: PathLike,
    report_step: AnalysingStepReport | None = None,
    report_progress: AnalysingProgressReport | None = None,
    correction_mode: CorrectionMode = CorrectionMode.NO,
    window_tokens: LLMWindowTokens | int | None = None,
    threads_count: int = 1,
  ) -> None:

  if correction_mode == CorrectionMode.DETAILED:
    print(
      "`correction_mode=CorrectionMode.DETAILED` is still experimental and not open to the public yet. " +
      "`correction_mode` will be forced to `CorrectionMode.ONCE`."
    )
    correction_mode = CorrectionMode.ONCE

  window_tokens = parse_window_tokens(window_tokens)
  threads = MultiThreads(threads_count)
  reporter = Reporter(
    report_step=report_step,
    report_progress=report_progress,
  )
  analysing_dir_path = Path(analysing_dir_path)
  ocr_path = analysing_dir_path / "ocr"
  assets_path = analysing_dir_path / "assets"
  sequence_path = analysing_dir_path / "sequence"
  correction_path = analysing_dir_path / "correction"
  contents_path = analysing_dir_path / "contents"
  chapter_path = analysing_dir_path / "chapter"
  reference_path = analysing_dir_path / "reference"

  generate_ocr_pages(
    extractor=pdf_page_extractor,
    reporter=reporter,
    pdf_path=Path(pdf_path),
    ocr_path=ocr_path,
    assets_path=assets_path,
  )
  extract_sequences(
    llm=llm,
    reporter=reporter,
    threads=threads,
    workspace_path=sequence_path,
    ocr_path=ocr_path,
    max_request_data_tokens=window_tokens.max_request_data_tokens,
    max_verify_paragraph_tokens=window_tokens.max_verify_paragraph_tokens,
    max_verify_paragraphs_count=window_tokens.max_verify_paragraphs_count,
  )
  sequence_output_path = sequence_path / "output"

  if correction_mode != CorrectionMode.NO:
    level: CorrectionLevel
    if correction_mode == CorrectionMode.ONCE:
      level = CorrectionLevel.Single
    elif correction_mode == CorrectionMode.DETAILED:
      level = CorrectionLevel.Multiple
    else:
      raise ValueError(f"Unknown correction mode: {correction_mode}")

    sequence_output_path = correct(
      llm=llm,
      reporter=reporter,
      threads=threads,
      level=level,
      workspace_path=correction_path,
      text_path=sequence_output_path / "text",
      footnote_path=sequence_output_path / "footnote",
      max_data_tokens=window_tokens.max_request_data_tokens,
    )

  reporter.go_to_step(AnalysingStep.EXTRACT_META)
  meta_path = extract_meta(
    llm=llm,
    workspace_path=analysing_dir_path / "meta",
    sequence_path=sequence_output_path / "text",
    max_request_tokens=window_tokens.max_request_data_tokens,
  )
  contents = extract_contents(
    llm=llm,
    reporter=reporter,
    workspace_path=contents_path,
    sequence_path=sequence_output_path / "text",
    max_data_tokens=window_tokens.max_request_data_tokens,
  )
  chapter_output_path, contents = generate_chapters(
    llm=llm,
    reporter=reporter,
    threads=threads,
    contents=contents,
    sequence_path=sequence_output_path / "text",
    workspace_path=chapter_path,
    max_request_tokens=window_tokens.max_request_data_tokens,
  )
  footnote_sequence_path = sequence_output_path / "footnote"

  if footnote_sequence_path.exists():
    chapter_output_path = generate_chapters_with_footnotes(
      reporter=reporter,
      chapter_path=chapter_output_path,
      footnote_sequence_path=footnote_sequence_path,
      workspace_path=reference_path,
    )

  reporter.go_to_step(AnalysingStep.OUTPUT)
  output(
    contents=contents,
    output_path=Path(output_dir_path),
    meta_path=meta_path,
    chapter_output_path=chapter_output_path,
    assets_path=assets_path,
  )
