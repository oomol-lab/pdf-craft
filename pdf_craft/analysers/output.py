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