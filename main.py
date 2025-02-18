import os
import shutil

from pdf_craft.pdf import stream_pdf, Text, TextKind
from doc_page_extractor import DocExtractor

def main():
  pdf_file = "/Users/taozeyu/Downloads/长觉能测试.pdf"
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", True)
  markdown_path = os.path.join("output", "output.md")
  extractor = DocExtractor(model_dir_path, order_by_layoutreader=False)

  with open(markdown_path, "w", encoding="utf-8") as file:
    for item in stream_pdf(extractor, pdf_file, output_dir_path):
      if not isinstance(item, Text):
        continue
      if item.kind == TextKind.TITLE:
        file.write("# " + item.text)
        file.write("\n\n")
      elif item.kind == TextKind.PLAIN_TEXT:
        file.write(item.text)
        file.write("\n\n")
      file.flush()

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()