import re

from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from ..common import XMLReader
from ..sequence import decode, Chapter, AssetLayout, ParagraphLayout, Reference
from .analyse import analyse_toc, RawChapter


@dataclass
class TocChapter:
    id: int
    title: str
    children: list["TocChapter"]


def generate_toc(chapters_path: Path) -> list[TocChapter]:
    chapters: XMLReader[Chapter] = XMLReader(
        prefix="chapter",
        dir_path=chapters_path,
        decode=decode,
    )
    top_items = analyse_toc(_to_raw_chapter(p) for p in enumerate(chapters.read()))
    return [_to_toc_chapter(item) for item in top_items]

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

def _to_toc_chapter(toc_item: TocItem[tuple[int, str, list[tuple[int, int, int, int]]]]) -> TocChapter:
    index, _, _ = toc_item.payload
    return TocChapter(
        id=index,
        title=toc_item.title,
        children=[_to_toc_chapter(child) for child in toc_item.children],
    )
