from pathlib import Path
from typing import Generator

from ..common import XMLReader, save_xml
from ..pdf import TITLE_TAGS, Page, decode
from ..toc import Toc, TocInfo, iter_toc
from .analyse_level import analyse_chapter_internal_levels
from .chapter import (
    AssetLayout,
    BlockLayout,
    Chapter,
    ParagraphLayout,
    Reference,
    encode,
)
from .content import expand_text_in_content, join_texts_in_content
from .jointer import Jointer
from .mark import Mark, search_marks
from .reference import References


def generate_chapter_files(pages_path: Path, chapters_path: Path, toc: TocInfo):
    chapters_path.mkdir(parents=True, exist_ok=True)
    for chapter_file in chapters_path.glob("chapter_*.xml"):
        chapter_file.unlink()

    for chapter in _generate_chapters(
        pages_path=pages_path,
        toc=toc,
    ):
        tail: str
        if chapter.id is None:
            tail = "head"
        else:
            tail = f"{chapter.id}"

        chapter = analyse_chapter_internal_levels(chapter)
        chapter_file = chapters_path / f"chapter_{tail}.xml"
        chapter_element = encode(chapter)
        save_xml(chapter_element, chapter_file)


def _generate_chapters(
    pages_path: Path, toc: TocInfo
) -> Generator[Chapter, None, None]:
    chapter: Chapter | None = None
    ref2toc: dict[tuple[int, int], Toc] = {}

    for item in iter_toc(toc.content):
        ref2toc[(item.page_index, item.order)] = item

    for layout in _extract_body_layouts(pages_path, toc):
        matched_toc = False
        if (
            isinstance(layout, ParagraphLayout)
            and layout.blocks
            and layout.ref in TITLE_TAGS
        ):
            item: Toc | None = None
            for block in layout.blocks:
                item = ref2toc.get((block.page_index, block.order), None)
                if item:
                    break
            if item:
                if chapter:
                    yield chapter
                chapter = Chapter(
                    id=item.id,
                    level=item.level,
                    layouts=[layout],
                )
                matched_toc = True

        if not matched_toc:
            if chapter is None:
                max_level = max((t.level for t in iter_toc(toc.content)), default=0)
                chapter = Chapter(
                    id=None,
                    level=max_level,  # 防止章节标题盖过其他
                    layouts=[],
                )
            chapter.layouts.append(layout)

    if chapter:
        yield chapter


def _extract_body_layouts(pages_path: Path, toc: TocInfo):
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode,
    )
    toc_page_indexes = set(toc.page_indexes)
    body_jointer = Jointer(
        (
            (p.index, p.body_layouts)
            for p in pages.read()
            if p.index not in toc_page_indexes
        )
    )
    footnotes_jointer = Jointer(
        (
            (p.index, p.footnotes_layouts)
            for p in pages.read()
            if p.index not in toc_page_indexes
        )
    )
    references_generator = _extract_page_references(footnotes_jointer)
    current_references: References | None = next(references_generator, None)

    def get_references(page_index: int) -> References | None:
        nonlocal current_references
        while (
            current_references is not None
            and current_references.page_index < page_index
        ):
            current_references = next(references_generator, None)
        if (
            current_references is not None
            and current_references.page_index == page_index
        ):
            return current_references
        return None

    for layout in body_jointer.execute():
        if isinstance(layout, ParagraphLayout):
            for block in layout.blocks:
                references = get_references(block.page_index)
                if references:
                    _replace_mark_with_reference(references, block)
                join_texts_in_content(block.content)

        yield layout


def _extract_page_references(jointer: Jointer) -> Generator[References, None, None]:
    last_page_index: int = -1
    layout_buffer: list[AssetLayout | ParagraphLayout] = []

    for layout in jointer.execute():
        page_index = _page_index_from_layout(layout)
        if page_index != last_page_index:
            if layout_buffer:
                yield References(
                    page_index=last_page_index,
                    layouts=layout_buffer,
                )
            last_page_index = page_index
            layout_buffer = []
        layout_buffer.append(layout)

    if layout_buffer:
        yield References(
            page_index=last_page_index,
            layouts=layout_buffer,
        )


def _page_index_from_layout(layout: AssetLayout | ParagraphLayout) -> int:
    if isinstance(layout, ParagraphLayout):
        if not layout.blocks:
            raise ValueError("ParagraphLayout has no blocks to get page index")
        return layout.blocks[0].page_index
    elif isinstance(layout, AssetLayout):
        return layout.page_index
    else:
        raise TypeError(f"Unknown layout type: {type(layout).__name__}")


def _replace_mark_with_reference(references: References, block: BlockLayout):
    def expand(text: str):
        for item in search_marks(text):
            reference: Reference | None = None
            if isinstance(item, Mark):
                reference = references.get(item)
            if reference:
                yield reference
            else:
                yield str(item)

    expand_text_in_content(
        content=block.content,
        expand=expand,
    )
