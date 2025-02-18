import os
import fitz
import shutil

from tqdm import tqdm
from pdf_craft import PDFPageExtractor, MarkDownWriter


def main():
  pdf_file = "/Users/taozeyu/Downloads/长觉能图片.pdf"
  model_dir_path = _project_dir_path("models")
  output_dir_path = _project_dir_path("output", True)
  markdown_path = os.path.join(output_dir_path, "output.md")
  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=model_dir_path,
    debug_dir_path=os.path.join("output", "plot"),
  )
  with MarkDownWriter(markdown_path, "images", "utf-8") as writer:
    with fitz.open(pdf_file) as pdf:
      for blocks in tqdm(extractor.extract(pdf, "ch"), total=pdf.page_count):
        for block in blocks:
          writer.write(block)
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