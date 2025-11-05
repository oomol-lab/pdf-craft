from pathlib import Path
from xml.etree.ElementTree import Element

from ..reader import BaseXMLReader
from .page import decode, Page


class PagesReader(BaseXMLReader[Page]):
    def __init__(self, pages_path: Path) -> None:
        super().__init__("page", pages_path)

    def _decode(self, element: Element) -> Page:
        return decode(element)