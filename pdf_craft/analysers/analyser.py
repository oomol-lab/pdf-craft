from os import PathLike
from pathlib import Path

from ..llm import LLM
from ..pdf import PDFPageExtractor

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
    correction: bool = False,
  ) -> None:

  max_data_tokens = 4096
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
    pdf_path=Path(pdf_path),
    ocr_path=ocr_path,
    assets_path=assets_path,
  )
  extract_sequences(
    llm=llm,
    workspace=sequence_path,
    ocr_path=ocr_path,
    max_data_tokens=max_data_tokens,
  )
  sequence_output_path = sequence_path / "output"

  if correction:
    sequence_output_path = correct(
      llm=llm,
      workspace=correction_path,
      text_path=sequence_output_path / "text",
      footnote_path=sequence_output_path / "footnote",
      max_data_tokens=max_data_tokens,
    )

  meta_path = extract_meta(
    llm=llm,
    workspace_path=analysing_dir_path / "meta",
    sequence_path=sequence_output_path / "text",
    max_request_tokens=max_data_tokens,
  )
  contents = extract_contents(
    llm=llm,
    workspace=contents_path,
    sequence_path=sequence_output_path / "text",
    max_data_tokens=max_data_tokens,
  )
  chapter_output_path, contents = generate_chapters(
    llm=llm,
    contents=contents,
    sequence_path=sequence_output_path / "text",
    workspace_path=chapter_path,
    max_request_tokens=max_data_tokens,
  )
  footnote_sequence_path = sequence_output_path / "footnote"

  if footnote_sequence_path.exists():
    chapter_output_path = generate_chapters_with_footnotes(
      chapter_path=chapter_output_path,
      footnote_sequence_path=footnote_sequence_path,
      workspace_path=reference_path,
    )

  output(
    contents=contents,
    output_path=Path(output_dir_path),
    meta_path=meta_path,
    chapter_output_path=chapter_output_path,
    assets_path=assets_path,
  )
