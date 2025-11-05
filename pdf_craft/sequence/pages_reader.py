import re

from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import fromstring

from ..pdf import decode, Page


_PAGE_FILE_REGEX = re.compile(r"^page_(\d+)\.xml$")

class PagesReader:
    def __init__(self, pages_path: Path) -> None:
        pages_path = Path(pages_path)
        indexed_files: list[tuple[int, Path]] = []
        for p in pages_path.glob("page_*.xml"):
            m = _PAGE_FILE_REGEX.match(p.name)
            if not m:
                continue
            idx = int(m.group(1))
            indexed_files.append((idx, p))

        indexed_files.sort(key=lambda t: t[0])
        self._file_paths: list[Path] = [path for _, path in indexed_files]


    def read(self) -> Generator[Page, None, None]:
        for xml_path in self._file_paths:
            try:
                root = fromstring(xml_path.read_text(encoding="utf-8"))
            except Exception as e:
                raise ValueError(f"Failed to parse XML file: {xml_path}") from e
            try:
                yield decode(root)
            except Exception as e:
                raise ValueError(f"Failed to decode Page from: {xml_path}") from e