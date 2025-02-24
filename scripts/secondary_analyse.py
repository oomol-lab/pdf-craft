import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft.analyser.llm import LLM
from pdf_craft.analyser.secondary import SecondaryAnalyser


def main():
  analyser = SecondaryAnalyser(
    llm=LLM(**_read_format_json()),
    dir_path="/Users/taozeyu/Downloads/并非旨在使人正常化的分析",
  )
  output_dir_path = os.path.join(__file__, "..", "..", "output", "citations")
  output_dir_path = os.path.abspath(output_dir_path)
  os.makedirs(output_dir_path, exist_ok=True)

  analyser.analyse_citations(output_dir_path, 10000, 0.15)

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()