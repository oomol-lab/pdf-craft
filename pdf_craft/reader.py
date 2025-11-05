import re

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator, Generic, TypeVar
from xml.etree.ElementTree import Element, fromstring


T = TypeVar("T")


class BaseXMLReader(ABC, Generic[T]):
    """Base class for reading indexed XML files and decoding them into objects."""

    def __init__(self, prefix: str, dir_path: Path) -> None:
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

    @abstractmethod
    def _decode(self, element: Element) -> T:
        pass

    def read(self) -> Generator[T, None, None]:
        for xml_path in self._file_paths:
            try:
                root = fromstring(xml_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise ValueError(f"Failed to parse XML file: {xml_path}") from e
            try:
                yield self._decode(root)
            except Exception as e:
                raise ValueError(f"Failed to decode from: {xml_path}") from e
