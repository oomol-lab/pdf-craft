import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft import PDFPageExtractor, MarkDownWriter


def main():
  pdf_file = "/Users/taozeyu/Downloads/丹药.pdf"
  output_dir_path = _project_dir_path("output", clean=True)
  markdown_path = os.path.join(output_dir_path, "output.md")
  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=_project_dir_path("models"),
  )
  with MarkDownWriter(markdown_path, "images", "utf-8") as md:
    for block in extractor.extract(pdf_file):
      md.write(block)

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()