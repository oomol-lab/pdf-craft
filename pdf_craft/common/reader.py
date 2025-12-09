import re

from pathlib import Path
from typing import Generator, Callable, Generic, TypeVar
from xml.etree.ElementTree import Element
from .xml import read_xml


T = TypeVar("T")


class XMLReader(Generic[T]):
    def __init__(self, prefix: str, dir_path: Path, decode: Callable[[Element], T]) -> None:
        dir_path = Path(dir_path)
        file_pattern = f"{prefix}_*.xml"
        regex = re.compile(rf"^{re.escape(prefix)}_(\d+)\.xml$")
        indexed_files: list[tuple[int, Path]] = []

        for p in dir_path.glob(file_pattern):
            m = regex.match(p.name)
            if not m:
                continue
            idx = int(m.group(1))
            indexed_files.append((idx, p))

        indexed_files.sort(key=lambda t: t[0])
        self._file_paths: list[Path] = [path for _, path in indexed_files]
        self._decode: Callable[[Element], T] = decode

    def read(self) -> Generator[T, None, None]:
        for xml_path in self._file_paths:
            root = read_xml(xml_path)
            try:
                yield self._decode(root)
            except Exception as e:
                raise ValueError(f"Failed to decode from: {xml_path}") from e
