from pathlib import Path
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

from .zip import Zip


def find_opf_path(zip: Zip) -> Path:
    container_path = Path("META-INF", "container.xml")

    with zip.read(container_path) as f:
        content = f.read()
        root = ET.fromstring(content)
        ns = {"ns": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = root.find(".//ns:rootfile", ns)

        if rootfile is None:
            rootfile = root.find(".//rootfile")

        if rootfile is None:
            raise ValueError("Cannot find rootfile in container.xml")

        full_path = rootfile.get("full-path")
        if full_path is None:
            raise ValueError("rootfile element has no full-path attribute")

        return Path(full_path)


def strip_namespace(elem: Element) -> None:
    if elem.tag.startswith("{"):
        elem.tag = elem.tag.split("}", 1)[1]

    for child in elem:
        strip_namespace(child)


def extract_namespace(tag: str) -> str | None:
    if tag.startswith("{"):
        parts = tag.split("}", 1)
        if len(parts) == 2:
            return parts[0][1:]
    return None
