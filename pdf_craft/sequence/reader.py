from pathlib import Path
from typing import Callable, Generator, Iterable

from ..common import XMLReader, read_xml
from .chapter import Chapter, decode


def create_chapters_reader(chapters_path: Path) -> Callable[[], Iterable[Chapter]]:
    chapters: XMLReader[Chapter] = XMLReader(
        prefix="chapter",
        dir_path=chapters_path,
        decode=decode,
    )
    head_path = chapters_path / "chapter_head.xml"

    def generate() -> Generator[Chapter, None, None]:
        if head_path.exists():
            root = read_xml(head_path)
            try:
                yield decode(root)
            except Exception as e:
                raise ValueError(f"Failed to decode from: {head_path}") from e

        yield from chapters.read()

    return generate
