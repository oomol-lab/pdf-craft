import os
import json

from pathlib import Path

from pdf_craft.llm import LLM
from pdf_craft.analysers.correction import correct


def main() -> None:
  llm=LLM(
    **_read_format_json(),
    log_dir_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/log"),
  )
  correct(
    llm=llm,
    workspace=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/correction"),
    text_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence/output/text"),
    footnote_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence/output/footnote"),
    max_data_tokens=4096,
  )

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()