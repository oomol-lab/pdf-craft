import os
import json

from pathlib import Path

from pdf_craft.llm import LLM
from pdf_craft.analysers.reference.footnote import generate_footnote_references


def main() -> None:
  generate_footnote_references(
    sequence_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence/output/footnote"),
    output_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/reference/footnote"),
  )

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()