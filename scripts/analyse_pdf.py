import os
import sys
import json
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from pdf_craft import analyse, LLM, PDFPageExtractor, AnalysingStep


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "citation.pdf")
  pdf_file = os.path.abspath(pdf_file)
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", clean=True)
  analysing_dir_path = _project_dir_path("analysing", clean=False)
  bar: tqdm | None = None

  try:
    def report_step(step: AnalysingStep, count: int):
      nonlocal bar
      if bar is not None:
        bar.close()
      print("Step:", step.name)
      bar = tqdm(total=count)

    def report_progress(completed_count: int):
      nonlocal bar
      if bar is not None:
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
        debug_dir_path=os.path.join(analysing_dir_path, "plot"),
      ),
    )
  finally:
    if bar is not None:
      bar.close()

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