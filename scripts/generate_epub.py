import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft.epub.gen_epub import generate_epub_file


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "figure-caption.pdf")
  pdf_file = os.path.abspath(pdf_file)
  output_dir_path = _project_dir_path("output")

  generate_epub_file(
    from_dir_path=output_dir_path,
    epub_file_path=os.path.join(output_dir_path, "final.epub"),
  )

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()