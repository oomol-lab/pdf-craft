import shutil

from json import dumps
from pathlib import Path
from .contents import Contents


def output(
    contents: Contents | None,
    output_path: Path,
    chapter_output_path: Path,
    assets_path: Path,
  ) -> None:

  if contents is not None:
    index_path = output_path / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
      f.write(dumps(contents.json(), ensure_ascii=False, indent=2))

  meta_path = output_path / "meta.json"
  with open(meta_path, "w", encoding="utf-8") as f:
    # TODO: complete metadata extraction logic
    meta = {
      "title": "Test book title",
      "authors": ["Tao Zeyu"],
    }
    f.write(dumps(meta, ensure_ascii=False, indent=2))

  cover_path = assets_path / "cover.png"
  if cover_path.exists():
    shutil.copy(cover_path, output_path / "cover.png")