import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft.epub.gen_files import generate_files


def main():
  pdf_file = os.path.join(__file__, "..", "..", "tests", "assets", "figure-caption.pdf")
  pdf_file = os.path.abspath(pdf_file)
  output_dir_path = _project_dir_path("output")
  epub_dir_path = os.path.join(output_dir_path, "epub")
  epub_file_path = os.path.join(output_dir_path, "final")

  shutil.rmtree(epub_dir_path, ignore_errors=True)
  os.makedirs(epub_dir_path, exist_ok=True)

  generate_files(
    from_dir_path=output_dir_path,
    output_dir_path=epub_dir_path,
  )
  shutil.make_archive(epub_file_path, "zip", epub_dir_path)
  os.rename(epub_file_path + ".zip", epub_file_path + ".epub")

def _project_dir_path(name: str, clean: bool = False) -> str:
  path = os.path.join(__file__, "..", "..", name)
  path = os.path.abspath(path)
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  os.makedirs(path, exist_ok=True)
  return path

if __name__ == "__main__":
  main()