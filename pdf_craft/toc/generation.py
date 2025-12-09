import re

from pathlib import Path
from typing import Generator

from ..common import save_xml, XMLReader
from ..sequence import decode, Chapter, AssetLayout, ParagraphLayout, Reference
from .item import encode
from .analyse import analyse_toc, RawChapter


def generate_toc_file(chapters_path: Path, toc_path: Path):
    chapters: XMLReader[Chapter] = XMLReader(
        prefix="chapter",
        dir_path=chapters_path,
        decode=decode,
    )
    toc_items = list(analyse_toc(
        chapters=(_to_raw_chapter(p) for p in enumerate(chapters.read())),
    ))
    toc_element = encode(toc_items)
    save_xml(toc_element, toc_path)

def _to_raw_chapter(pair: tuple[int, Chapter]) -> RawChapter:
    index, chapter = pair
    return RawChapter(
        id=index + 1,
        title=_title_of_chapter(chapter),
        det=list(_search_det_in_chapter(chapter)),
    )


def _title_of_chapter(chapter: Chapter) -> str:
    title = chapter.title
    if not title:
        return ""
    result_parts: list[str] = []
    for line in title.lines:
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
    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            yield layout.det
        elif isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                yield line.det
