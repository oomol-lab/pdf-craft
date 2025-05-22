import os
import json
import shutil

from pathlib import Path
from pdf_craft.llm import LLM
from pdf_craft import OCRLevel, PDFPageExtractor
from pdf_craft.analysers import analyse


def main() -> None:
  llm=LLM(
    **_read_format_json(),
    log_dir_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/log"),
  )
  extractor=PDFPageExtractor(
    device="cpu",
    ocr_level=OCRLevel.OncePerLayout,
    model_dir_path=str(_project_dir_path("models")),
    debug_dir_path=str(_project_dir_path("analysing") / "plot"),
  )
  analyse(
    llm=llm,
    pdf_page_extractor=extractor,
    pdf_path=Path("/Users/taozeyu/Downloads/pdf-craft 问题pdf文件搜集/美国工人运动史张友伦.pdf"),
    analysing_dir_path=_project_dir_path("analysing"),
    output_path=_project_dir_path("output", clean=True),
  )

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

def _project_dir_path(name: str, clean: bool = False) -> Path:
  path = Path(__file__) / ".." / name
  path = path.resolve()
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  path.mkdir(parents=True, exist_ok=True)
  return path

if __name__ == "__main__":
  main()