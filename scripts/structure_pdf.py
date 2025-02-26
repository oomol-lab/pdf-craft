import os
import sys
import fitz
import json
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from typing import Generator
from pdf_craft import PDFPageExtractor, Block
from pdf_craft.analyser.preliminary import preliminary_analyse
from pdf_craft.analyser.llm import LLM


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "index.pdf")
  pdf_file = os.path.abspath(pdf_file)
  # pdf_file = "/Users/taozeyu/Downloads/中国古代农业.pdf"
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", clean=True)
  page_dir_path = os.path.join(output_dir_path, "pages")
  assets_dir_path = os.path.join(output_dir_path, "assets")

  for dir_path in (page_dir_path, assets_dir_path):
    os.makedirs(dir_path, exist_ok=True)

  llm=LLM(**_read_format_json())
  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=model_dir_path,
    debug_dir_path=os.path.join("output", "plot"),
  )
  preliminary_analyse(
    llm=llm,
    page_dir_path=page_dir_path,
    assets_dir_path=assets_dir_path,
    blocks_matrix=_extract_blocks(pdf_file, extractor),
  )

def _extract_blocks(pdf_file: str, extractor: PDFPageExtractor) -> Generator[list[Block], None, None]:
  with fitz.open(pdf_file) as pdf:
    for blocks in tqdm(
      iterable=extractor.extract(pdf, "ch"),
      total=pdf.page_count,
    ):
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