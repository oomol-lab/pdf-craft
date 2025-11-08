from pathlib import Path
from typing import Generator

from ..common import save_xml, XMLReader
from ..pdf import decode, Page
from .jointer import TITLE_TAGS, Jointer
from .chapter import encode, Reference, Chapter, AssetLayout, ParagraphLayout, LineLayout
from .reference import References
from .mark import search_marks, Mark


def generate_chapter_files(pages_path: Path, chapters_path: Path):
    chapters_path.mkdir(parents=True, exist_ok=True)
    for chapter_file in chapters_path.glob("chapter_*.xml"):
        chapter_file.unlink()

    for i, chapter in enumerate(_generate_chapters(pages_path)):
        chapter_file = chapters_path / f"chapter_{i + 1}.xml"
        chapter_element = encode(chapter)
        save_xml(chapter_element, chapter_file)

def _generate_chapters(pages_path: Path):
    chapter: Chapter | None = None
    for layout in _extract_body_layouts(pages_path):
        if isinstance(layout, ParagraphLayout) and layout.ref in TITLE_TAGS:
            if chapter:
                yield chapter
            chapter = Chapter(title=layout, layouts=[])
        else:
            if chapter is None:
                chapter = Chapter(title=None, layouts=[])
            chapter.layouts.append(layout)

    if chapter:
        yield chapter

def _extract_body_layouts(pages_path: Path):
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode,
    )
    body_jointer = Jointer(((p.index, p.body_layouts) for p in pages.read()))
    footnotes_jointer = Jointer(((p.index, p.footnotes_layouts) for p in pages.read()))
    references_generator = _extract_page_references(footnotes_jointer)
    current_references: References | None = next(references_generator, None)

    def get_references(page_index: int) -> References | None:
        nonlocal current_references
        while current_references is not None and current_references.page_index < page_index:
            current_references = next(references_generator, None)
        if current_references is not None and current_references.page_index == page_index:
            return current_references
        return None

    for layout in body_jointer.execute():
        if isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                references = get_references(line.page_index)
                if references:
                    line.content = list(_line_parts_after_replace_references(references, line))
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
        return layout.lines[0].page_index
    elif isinstance(layout, AssetLayout):
        return layout.page_index
    else:
        raise ValueError("Unknown layout type")


def _line_parts_after_replace_references(references: References, line: LineLayout) -> Generator[str | Reference, None, None]:
    text_buffer: list[str] = []
    for part in _search_mark_and_split_line(references, line.content):
        if isinstance(part, str):
            text_buffer.append(part)
        elif text_buffer:
            yield "".join(text_buffer)
            text_buffer = []
            yield part
    if text_buffer:
        yield "".join(text_buffer)

def _search_mark_and_split_line(references: References, parts: list[str | Reference]):
    for part in parts:
        if isinstance(part, Reference):
            yield part
        else:
            for item in search_marks(part):
                reference: Reference | None = None
                if isinstance(item, Mark):
                    reference = references.get(item)
                if reference:
                    yield reference
                else:
                    yield str(item)