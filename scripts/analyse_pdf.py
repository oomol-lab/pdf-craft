import os
import sys
import fitz
import json
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from typing import Generator
from pdf_craft import analyse, PDFPageExtractor, Block, LLM


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "figure-caption.pdf")
  pdf_file = os.path.abspath(pdf_file)
  # pdf_file = "/Users/taozeyu/Downloads/混编书籍.pdf"
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", clean=True)

  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=model_dir_path,
    debug_dir_path=os.path.join("output", "plot"),
  )
  analyse(
    llm=LLM(**_read_format_json()),
    analysing_dir_path=os.path.join(output_dir_path, "analysing"),
    output_dir_path=output_dir_path,
    blocks_matrix=_extract_blocks(
      pdf_file=pdf_file,
      extractor=extractor,
      cover_path=os.path.join(output_dir_path, "assets", "cover.png"),
    ),
  )

def _extract_blocks(pdf_file: str, extractor: PDFPageExtractor, cover_path: str) -> Generator[list[Block], None, None]:
  did_save_cover = False

  with fitz.open(pdf_file) as pdf:
    for blocks, image in tqdm(
      iterable=extractor.extract(pdf, "ch"),
      total=pdf.page_count,
    ):
      if not did_save_cover:
        image.save(cover_path)
        did_save_cover = True

      yield blocks

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()