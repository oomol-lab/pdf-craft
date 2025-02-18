import os
import shutil

from pdf_craft import stream_pdf, MarkDownWriter
from doc_page_extractor import DocExtractor

def main():
  pdf_file = "/Users/taozeyu/Downloads/长觉能图片.pdf"
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", True)
  markdown_path = os.path.join(output_dir_path, "output.md")
  debug_dir_path = os.path.join("output", "plot")
  extractor = DocExtractor(model_dir_path, order_by_layoutreader=False)

  with MarkDownWriter(markdown_path, "images", "utf-8") as writer:
    for item in stream_pdf(extractor, pdf_file, debug_dir_path):
      writer.write(item)
      writer.flush()

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()