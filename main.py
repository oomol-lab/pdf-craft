import os
import fitz
import json
import shutil

from tqdm import tqdm
from pdf_craft import PDFPageExtractor
from pdf_craft.ai.format import Format


def main():
  pdf_file = "/Users/taozeyu/Downloads/引用文献强化.pdf"
  model_dir_path = _project_dir_path("models")
  format = Format(**_read_format_json())
  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=model_dir_path,
    debug_dir_path=os.path.join("output", "plot"),
  )
  with fitz.open(pdf_file) as pdf:
    for blocks in tqdm(extractor.extract(pdf, "ch"), total=pdf.page_count):
      print("\n======================")
      format.push(blocks)

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()