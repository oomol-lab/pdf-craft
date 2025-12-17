from dataclasses import dataclass
from typing import Iterable, Generator
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
        heading_levels=list(_assign_heading_levels(heading_info)),
    )


def _assign_heading_levels(heading_info: list[tuple[RawChapter, str, float]]) -> Generator[int, None, None]:
    if len(heading_info) <= 1:
        for _ in heading_info:
            yield 1
        return

    heights = [h for _, _, h in heading_info]
    unique_heights = sorted(set(heights), reverse=True)
    max_levels = min(6, len(unique_heights))

    if len(unique_heights) <= max_levels:
        height_to_level = {h: i + 1 for i, h in enumerate(unique_heights)}
    else:
        clustered_heights = list(_cluster_heights(unique_heights, max_levels))
        height_to_level = {}
        for i, cluster in enumerate(clustered_heights):
            for h in cluster:
                height_to_level[h] = i + 1

    for _, _, h in heading_info:
        yield height_to_level[h]


def _cluster_heights(heights: list[float], max_clusters: int) -> Generator[list[float], None, None]:
    if len(heights) <= max_clusters:
        for h in heights:
            yield [h]
        return

    gaps = [(heights[i] - heights[i + 1], i) for i in range(len(heights) - 1)]
    gaps.sort(reverse=True)
    split_indices = sorted([idx + 1 for _, idx in gaps[:max_clusters - 1]])

    start = 0
    for split_idx in split_indices:
        yield heights[start:split_idx]
        start = split_idx
    yield heights[start:]


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
