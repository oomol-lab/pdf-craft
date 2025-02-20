import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from pdf_craft.analyser.llm import LLM
from pdf_craft.analyser.secondary import SecondaryAnalyser


def main():
  struct_dir_path = "/Users/taozeyu/Downloads/并非旨在使人正常化的分析"
  llm=LLM(**_read_format_json())
  analyser = SecondaryAnalyser(llm, struct_dir_path)

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()