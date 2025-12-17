import json
import re

from pathlib import Path
from typing import Generator

from ...common import XMLReader
from ...pdf import decode, Page, TITLE_TAGS
from .finder import find_toc_page_indexes

_TITLE_MARKDOWN_HEAD_PATTER = re.compile(r"^\s*#{1,6}\s*")

def analyse_toc_range(pages_path: Path, toc_path: Path) -> list[int]:
    if toc_path.exists():
        with open(toc_path, "r", encoding="utf-8") as f:
            return json.load(f)

    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode,
    )
    page_indexes = find_toc_page_indexes(
        iter_titles=lambda:_search_titles(pages),
        iter_page_bodies=lambda:_search_body(pages),
    )
    toc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(toc_path, "w", encoding="utf-8") as f:
        json.dump(page_indexes, f)

    return page_indexes

def _search_titles(pages: XMLReader[Page]) -> Generator[list[str], None, None]:
    for page in pages.read():
        yield list(
            _TITLE_MARKDOWN_HEAD_PATTER.sub("", layout.text)
            for layout in page.body_layouts
            if layout.ref in TITLE_TAGS
        )

def _search_body(pages: XMLReader[Page]) -> Generator[str, None, None]:
    for page in pages.read():
        yield "".join(layout.text for layout in page.body_layouts)
