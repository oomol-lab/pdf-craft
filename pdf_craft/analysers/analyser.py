from os import PathLike
from pathlib import Path

from ..llm import LLM
from ..pdf import PDFPageExtractor

from .reporter import Reporter, AnalysingStep, AnalysingStepReport, AnalysingProgressReport
from .ocr import generate_ocr_pages
from .sequence import extract_sequences
from .correction import correct
from .meta import extract_meta
from .contents import extract_contents
from .chapter import generate_chapters
from .reference import generate_chapters_with_footnotes
from .output import output


def analyse(
    llm: LLM,
    pdf_page_extractor: PDFPageExtractor,
    pdf_path: PathLike,
    analysing_dir_path: PathLike,
    output_dir_path: PathLike,
    report_step: AnalysingStepReport | None = None,
    report_progress: AnalysingProgressReport | None = None,
    correction: bool = False,
  ) -> None:

  max_data_tokens = 4096
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
    workspace_path=sequence_path,
    ocr_path=ocr_path,
    max_data_tokens=max_data_tokens,
  )
  sequence_output_path = sequence_path / "output"

  if correction:
    sequence_output_path = correct(
      llm=llm,
      reporter=reporter,
      workspace_path=correction_path,
      text_path=sequence_output_path / "text",
      footnote_path=sequence_output_path / "footnote",
      max_data_tokens=max_data_tokens,
    )

  reporter.go_to_step(AnalysingStep.EXTRACT_META)
  meta_path = extract_meta(
    llm=llm,
    workspace_path=analysing_dir_path / "meta",
    sequence_path=sequence_output_path / "text",
    max_request_tokens=max_data_tokens,
  )
  contents = extract_contents(
    llm=llm,
    reporter=reporter,
    workspace_path=contents_path,
    sequence_path=sequence_output_path / "text",
    max_data_tokens=max_data_tokens,
  )
  chapter_output_path, contents = generate_chapters(
    llm=llm,
    reporter=reporter,
    contents=contents,
    sequence_path=sequence_output_path / "text",
    workspace_path=chapter_path,
    max_request_tokens=max_data_tokens,
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
