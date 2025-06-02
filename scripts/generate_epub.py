import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pathlib import Path
from pdf_craft import generate_epub_file, LaTeXRender


def main():
  output_dir_path = _project_dir_path("output")
  generate_epub_file(
    from_dir_path=output_dir_path,
    epub_file_path=output_dir_path / "final.epub",
    latex_render=LaTeXRender.MATHML,
  )

def _project_dir_path(name: str, clean: bool = False) -> Path:
  path = Path(__file__) / ".." / ".." / name
  path = path.resolve()
  if clean:
    shutil.rmtree(path, ignore_errors=True)
  path.mkdir(parents=True, exist_ok=True)
  return path

if __name__ == "__main__":
  main()