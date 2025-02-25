import os
import sys
import fitz
import json
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from pdf_craft import PDFPageExtractor
from pdf_craft.analyser.page_structure import structure
from pdf_craft.analyser.llm import LLM


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "index.pdf")
  pdf_file = os.path.abspath(pdf_file)
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
  with fitz.open(pdf_file) as pdf:
    for i, blocks in enumerate(tqdm(
      iterable=extractor.extract(pdf, "ch"),
      total=pdf.page_count,
    )):
      output_file_path = os.path.join(page_dir_path, f"page_{i+1}.xml")
      structure(
        llm,
        blocks,
        output_file_path,
        assets_dir_path,
      )

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