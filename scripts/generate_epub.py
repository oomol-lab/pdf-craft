import os
import sys
import shutil

sys.path.append(os.path.abspath(os.path.join(__file__, "..", "..")))

from pdf_craft import generate_epub_file, LaTeXRender


def main():
  output_dir_path = _project_dir_path("output")
  generate_epub_file(
    from_dir_path=output_dir_path,
    epub_file_path=os.path.join(output_dir_path, "final.epub"),
    latex_render=LaTeXRender.SVG,
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