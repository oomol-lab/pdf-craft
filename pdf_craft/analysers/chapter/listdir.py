from pathlib import Path
from typing import Generator
from ..utils import xml_files


def list_chapter_files(chapter_path: Path) -> Generator[tuple[int | None, Path], None, None]:
  head_path = chapter_path.joinpath("chapter.xml")
  if head_path.exists():
    yield None, head_path

  for path, prefix, chapter_id, _ in xml_files(chapter_path):
    if prefix == "chapter":
      yield chapter_id, path