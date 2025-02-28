import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft.analyser.llm import LLM
from pdf_craft.analyser.secondary import SecondaryAnalyser


def main():
  dir_path = os.path.join(__file__, "..", "..", "output")
  dir_path = os.path.abspath(dir_path)
  analyser = SecondaryAnalyser(
    llm=LLM(**_read_format_json()),
    dir_path=dir_path,
  )
  analyser.analyse_citations(10000, 0.15)

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()