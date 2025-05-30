import os
import sys
import json
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from pathlib import Path
from pdf_craft import analyse, LLM, OCRLevel, PDFPageExtractor, AnalysingStep


def main():
  pdf_file = Path(__file__) / ".." / ".." / "tests" / "assets" / "citation_large.pdf"
  pdf_file = pdf_file.resolve()
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", clean=True)
  analysing_dir_path = _project_dir_path("analysing", clean=False)
  bar: tqdm | None = None

  try:
    def report_step(step: AnalysingStep):
      nonlocal bar
      bar = None
      print("Step:", step.name)

    def report_progress(completed_count: int, max_count: int | None):
      nonlocal bar
      if bar is None:
        bar = tqdm(total=max_count)
      bar.update(completed_count)

    analyse(
      llm=LLM(**_read_format_json()),
      pdf_path=pdf_file,
      analysing_dir_path=analysing_dir_path,
      output_dir_path=output_dir_path,
      report_step=report_step,
      report_progress=report_progress,
      pdf_page_extractor=PDFPageExtractor(
        device="cpu",
        model_dir_path=model_dir_path,
        ocr_level=OCRLevel.OncePerLayout,
        debug_dir_path=os.path.join(analysing_dir_path, "plot"),
      ),
    )
  finally:
    if bar is not None:
      bar.close()

def _read_format_json() -> dict:
  path = Path(__file__) / ".." / ".." / "format.json"
  path = path.resolve()
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

def _project_dir_path(name: str, clean: bool = False) -> Path:
  path = Path(__file__) / ".." / ".." / name
  path = path.resolve()
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  path.mkdir(parents=True, exist_ok=True)
  return path

if __name__ == "__main__":
  main()