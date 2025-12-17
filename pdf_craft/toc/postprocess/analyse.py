from dataclasses import dataclass
from typing import Iterable, Generator

from ..common import calculate_levels
from .item import TocItem


@dataclass
class RawChapter:
    id: int
    title: str
    det: list[tuple[int, int, int, int]]


def analyse_toc(chapters: Iterable[RawChapter]) -> Generator[TocItem, None, None]:
    heading_info: list[tuple[RawChapter, str, float]] = []
    for chapter in chapters:
        if not chapter.det:
            continue
        heights = [det[3] - det[1] for det in chapter.det]  # y2 - y1
        avg_height = sum(heights) / len(heights) if heights else 0

        if avg_height > 0:
            heading_info.append((chapter, chapter.title, avg_height))

    if not heading_info:
        return

    yield from _build_toc_tree(
        heading_info=heading_info,
        heading_levels=list(calculate_levels(
            heights=[h for _, _, h in heading_info],
        )),
    )

def _build_toc_tree(
    heading_info: list[tuple[RawChapter, str, float]],
    heading_levels: list[int]
) -> Generator[TocItem, None, None]:
    if not heading_info:
        return

    root_items: list[TocItem] = []
    stack: list[tuple[int, TocItem]] = []

    for (chapter, title, _), level in zip(heading_info, heading_levels):
        new_item = TocItem(id=chapter.id, title=title, children=[])
        while stack and stack[-1][0] >= level:
            stack.pop()

        if stack:
            parent_item = stack[-1][1]
            parent_item.children.append(new_item)
        else:
            root_items.append(new_item)

        stack.append((level, new_item))

    yield from root_items
