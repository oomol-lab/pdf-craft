from typing import Iterable, Callable

from ...common import split_by_cv
from ...sequence import Chapter, BlockLayout

from ..common import PageRef, TitleReference


_MAX_TOC_LEVELS = 4

def analyse_toc2(
        iter_chapters: Callable[[], Iterable[Chapter]],
        toc_page_refs: list[PageRef] | None,
    ):
    print(toc_page_refs)
    result = _split_titles_from_chapters(iter_chapters())

    print("\n\n")
    for level, titles in enumerate(result, start=1):
        print(f"Level {level}:")
        for title_ref in titles:
            print(f"  Page {title_ref.page_index}, Order {title_ref.order}: {title_ref.content}")

def _split_titles_from_chapters(chapters: Iterable[Chapter]) -> list[list[BlockLayout]]:
    to_split: list[tuple[float, BlockLayout]] = []
    for chapter in chapters:
        title = chapter.title
        if not title or not title.blocks:
            continue
        block = title.blocks[0]
        _, top, _, bottom = block.det
        height = bottom - top
        to_split.append((float(height), block))

    return [
        sorted(layouts, key=lambda x: (x.page_index, x.order))
        for layouts in split_by_cv(
            payload_items=to_split,
            max_groups=_MAX_TOC_LEVELS,
        )
    ]

