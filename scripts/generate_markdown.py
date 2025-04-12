import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from tqdm import tqdm
from pdf_craft import PDFPageExtractor, MarkDownWriter, ExtractedTableFormat


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "table&formula.pdf")
  pdf_file = os.path.abspath(pdf_file)
  output_dir_path = _project_dir_path("output", clean=True)
  markdown_path = os.path.join(output_dir_path, "output.md")
  extractor = PDFPageExtractor(
    device="cpu",
    model_dir_path=_project_dir_path("models"),
    extract_table_format=ExtractedTableFormat.MARKDOWN,
  )
  bar: tqdm | None = None
  try:
    def report_progress(i: int, n: int):
      nonlocal bar
      if bar:
        bar.update(i)
      else:
        bar = tqdm(total=n)

    with MarkDownWriter(markdown_path, "images", "utf-8") as md:
      for block in extractor.extract(pdf_file, report_progress=report_progress):
        md.write(block)

  finally:
    if bar:
      bar.close()

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()