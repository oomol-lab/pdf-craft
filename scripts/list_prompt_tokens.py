import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft.llm import LLM


def main():
  llm = LLM(**_read_format_json())
  for template_name in ("main_text", "citation", "page", "index", "position"):
    tokens = llm.prompt_tokens_count(template_name, {
      "index": "",
    })
    print(template_name, tokens)

def _read_format_json() -> dict:
  path = os.path.join(__file__, "..", "..", "format.json")
  path = os.path.abspath(path)
  with open(path, mode="r", encoding="utf-8") as file:
    return json.load(file)

if __name__ == "__main__":
  main()