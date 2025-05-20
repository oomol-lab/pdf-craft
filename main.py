import os
import json

from pathlib import Path

from pdf_craft.llm import LLM
from pdf_craft.analysers.reference.footnote import generate_footnote_references, append_footnote_for_chapters


def main() -> None:
  generate_footnote_references(
    sequence_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/sequence/output/footnote"),
    output_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/reference/footnote"),
  )
  append_footnote_for_chapters(
    chapter_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/chapter/output"),
    footnote_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/reference/footnote"),
    output_path=Path("/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/analysing/reference/output"),
  )

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()