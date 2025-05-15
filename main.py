import os
import json
from pathlib import Path

from pdf_craft.llm import LLM
from pdf_craft.analysers.contents import extract_contents


def main() -> None:
  contents = extract_contents(
    llm=LLM(
      **_read_format_json(),
      log_file_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/request.log"),
    ),
    workspace=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/contents"),
    sequence_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence/output/text"),
    max_data_tokens=4096,
  )
  print(contents)

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()