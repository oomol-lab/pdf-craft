import os
import json
from pathlib import Path

from pdf_craft.llm import LLM
from pdf_craft.analysers.sequence import extract_sequences


def main() -> None:
  extract_sequences(
    llm=LLM(
      **_read_format_json(),
      log_file_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/request.log"),
    ),
    workspace=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence"),
    ocr_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/ocr"),
    max_data_tokens=4096,
  )

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()