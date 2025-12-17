import re

from pathlib import Path
from typing import Generator

from ...common import read_xml, save_xml, XMLReader
from ...sequence import decode as decode_chapter, Chapter, Reference

from ..common import decode as decode_toc
from .item import encode
from .analyse import analyse_toc, RawChapter
from .analyse2 import analyse_toc2


def generate_toc_file(
        chapters_path: Path,
        toc_pages_path: Path,
        toc_analysed_path: Path,
    ) -> Path | None:
    chapters: XMLReader[Chapter] = XMLReader(
        prefix="chapter",
        dir_path=chapters_path,
        decode=decode_chapter,
    )
    analyse_toc2(
        iter_chapters=lambda: (c for c in chapters.read()),
        toc_page_refs=decode_toc(read_xml(toc_pages_path)),
    )
    raw_chapters = (_to_raw_chapter(p) for p in enumerate(chapters.read()))
    toc_element = encode(analyse_toc(
        chapters=(c for c in raw_chapters if c is not None),
    ))
    if toc_element is None:
        return None
    save_xml(toc_element, toc_analysed_path)
    return toc_analysed_path


def _to_raw_chapter(pair: tuple[int, Chapter]) -> RawChapter | None:
    index, chapter = pair
    title = _title_of_chapter(chapter)
    if title is None:
        return None
    return RawChapter(
        id=index + 1,
        title=title,
        det=list(_search_det_in_chapter(chapter)),
    )


def _title_of_chapter(chapter: Chapter) -> str | None:
    title = chapter.title
    if not title:
        return None
    result_parts: list[str] = []
    for line in title.blocks:
        for part in line.content:
            if isinstance(part, str):
                result_parts.append(part)
            elif isinstance(part, Reference):
                # 这里要添加到目录中，没有必要显示引用标记
                pass

    raw_title = "".join(result_parts)
    normalized_title = re.sub(r"\s+", " ", raw_title).strip()
    return normalized_title

def _search_det_in_chapter(chapter: Chapter) -> Generator[tuple[int, int, int, int], None, None]:
    title = chapter.title
    if title is None:
        return
    for line in title.blocks:
        yield line.det
