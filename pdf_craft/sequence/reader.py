from pathlib import Path
from xml.etree.ElementTree import Element

from ..common import BaseXMLReader
from .chapter import Chapter, decode


class ChapterReader(BaseXMLReader[Chapter]):
    def __init__(self, chapters_path: Path) -> None:
        super().__init__("chapter", chapters_path)

    def _decode(self, element: Element) -> Chapter:
        return decode(element)