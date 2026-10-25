"""Shared, lazy input parsing for EPUB assertions and translation."""
from collections.abc import Iterator
from pathlib import Path

from ....transformer.xml_translator.xml import XMLLikeNode
from .spines import search_spine_paths
from .zip import ReadOnlyZip


def read_chapters(archive: ReadOnlyZip) -> Iterator[tuple[Path, str, XMLLikeNode]]:
    for path, media_type in search_spine_paths(archive):
        with archive.read(path) as file:
            xml = XMLLikeNode(file=file, is_html_like=(media_type == "text/html"))
        yield path, media_type, xml
