from pathlib import Path
from typing import Generator, Iterable

from ...common import XMLReader, save_xml
from ...pdf import Page, decode
from ..toc import Toc, TocInfo, iter_toc
from .analyse_level import analyse_chapter_internal_levels
from .chapter import (
    Chapter, DisplayFormula, FlowItem, Reference, SourceAsset,
    SourceTextFragment, StandaloneAsset, TextFlowItem, encode,
)
from .content import expand_text_in_content, join_texts_in_content
from .jointer import Jointer
from .mark import Mark, search_marks
from .mergeable import check_mergeable
from .page_analysis import UnindexedCitation, analyse_pages, restore_streams
from .page_review import (
    PageAnalysisProcessor,
    load_page_pixel_sizes,
)
from .punctuation import normalize_punctuation_in_chapter
from .reference import References, extract_head_mark


def generate_chapter_files(
    pages_path: Path,
    chapters_path: Path,
    toc: TocInfo,
    page_analysis_processor: PageAnalysisProcessor | None = None,
):
    chapters_path.mkdir(parents=True, exist_ok=True)
    for chapter_file in chapters_path.glob("chapter_*.xml"):
        chapter_file.unlink()

    for chapter in _generate_chapters(
        pages_path=pages_path,
        toc=toc,
        page_analysis_processor=page_analysis_processor,
    ):
        tail: str
        if chapter.id is None:
            tail = "head"
        else:
            tail = f"{chapter.id}"

        chapter = normalize_punctuation_in_chapter(chapter)
        chapter = analyse_chapter_internal_levels(chapter)
        chapter_file = chapters_path / f"chapter_{tail}.xml"
        chapter_element = encode(chapter)
        save_xml(chapter_element, chapter_file)


def _generate_chapters(
    pages_path: Path,
    toc: TocInfo,
    page_analysis_processor: PageAnalysisProcessor | None = None,
) -> Generator[Chapter, None, None]:
    chapter: Chapter | None = None
    ref2toc: dict[tuple[int, int], Toc] = {}

    for item in iter_toc(toc.content):
        ref2toc[(item.page_index, item.order)] = item

    for layout in _assemble_flow_items(_extract_body_layouts(
        pages_path, toc, page_analysis_processor
    )):
        matched_toc = False
        if (
            isinstance(layout, TextFlowItem)
            and layout.children
            and layout.role == "heading"
        ):
            item: Toc | None = None
            for fragment in layout.children:
                if not isinstance(fragment, SourceTextFragment):
                    continue
                item = ref2toc.get((fragment.page_index, fragment.source_order), None)
                if item:
                    break
            if item:
                if chapter:
                    yield chapter
                chapter = Chapter(
                    id=item.id,
                    level=item.level,
                    flow_items=[layout],
                )
                matched_toc = True

        if not matched_toc:
            if chapter is None:
                max_level = max((t.level for t in iter_toc(toc.content)), default=0)
                chapter = Chapter(
                    id=None,
                    level=max_level,  # 防止章节标题盖过其他
                    flow_items=[],
                )
            chapter.flow_items.append(layout)

    if chapter:
        yield chapter


def _extract_body_layouts(
    pages_path: Path,
    toc: TocInfo,
    page_analysis_processor: PageAnalysisProcessor | None = None,
):
    pages: XMLReader[Page] = XMLReader(
        prefix="page",
        dir_path=pages_path,
        decode=decode,
    )
    toc_page_indexes = set(toc.page_indexes)
    source_pages = [
        page for page in pages.read() if page.index not in toc_page_indexes
    ]
    paragraphs, citations = _resolve_pages(source_pages)
    analysed_pages = analyse_pages(
        page_indexes=(page.index for page in source_pages),
        paragraphs=paragraphs,
        citations=citations,
    )
    if page_analysis_processor is not None:
        analysed_pages = page_analysis_processor(
            source_pages,
            analysed_pages,
            load_page_pixel_sizes(pages_path / "page_pixel_sizes.json"),
        )
    yield from restore_streams(analysed_pages).paragraphs


def _resolve_pages(
    pages: Iterable[Page],
) -> tuple[
    list[TextFlowItem | SourceAsset],
    list[Reference | UnindexedCitation],
]:
    """Run the traditional algorithms up to the reversible page boundary."""

    page_list = list(pages)
    body_jointer = Jointer(
        (page.index, page.body_layouts) for page in page_list
    )
    footnotes_jointer = Jointer(
        (page.index, page.footnotes_layouts) for page in page_list
    )
    page_references = list(_extract_page_references(footnotes_jointer))
    references_generator = iter(page_references)
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

    paragraphs: list[TextFlowItem | SourceAsset] = []
    for layout in body_jointer.execute():
        if isinstance(layout, TextFlowItem):
            for fragment in layout.children:
                if not isinstance(fragment, SourceTextFragment):
                    continue
                references = get_references(fragment.page_index)
                if references:
                    _replace_mark_with_reference(references, fragment)
                join_texts_in_content(fragment.content)

        paragraphs.append(layout)

    citations: list[Reference | UnindexedCitation] = []
    for references in page_references:
        if references.unindexed_items:
            citations.append(
                UnindexedCitation(
                    page_index=references.page_index,
                    flow_items=references.unindexed_items,
                )
            )
        citations.extend(references.values)
    return paragraphs, citations


def _assemble_flow_items(
    layouts: Iterable[TextFlowItem | SourceAsset],
) -> Generator[FlowItem, None, None]:
    """Turn the joiner's conservative flat OCR sequence into v3 flow.

    The joiner has already decided whether neighbouring OCR text regions form
    one author paragraph.  Here we preserve a figure/table found between two
    mergeable text runs as a child of that paragraph.  Equations are never
    candidates: they are hard reading-flow boundaries.
    """
    current: TextFlowItem | None = None
    pending: list[SourceAsset] = []

    def flush() -> Generator[FlowItem, None, None]:
        nonlocal current, pending
        if current is not None:
            yield current
            current = None
        for asset in pending:
            yield DisplayFormula(asset) if asset.ref == "formula" else StandaloneAsset(asset)
        pending = []

    for layout in layouts:
        if isinstance(layout, SourceAsset):
            pending.append(layout)
            continue
        text = layout
        if current is None:
            # Leading assets are standalone and must remain before the text.
            yield from flush()
            current = text
            continue
        can_embed = (
            pending
            and not any(asset.ref == "formula" for asset in pending)
            and current.role == text.role == "body"
            and current.children and text.children
            and isinstance(current.children[-1], SourceTextFragment)
            and isinstance(text.children[0], SourceTextFragment)
            and check_mergeable(current.children[-1].content, text.children[0].content)
        )
        if can_embed:
            current.children.extend(pending)
            current.children.extend(text.children)
            pending = []
            continue
        yield from flush()
        current = text
    yield from flush()


def _extract_page_references(jointer: Jointer) -> Generator[References, None, None]:
    last_page_index: int = -1
    layout_buffer: list[SourceAsset | TextFlowItem] = []

    for layout in jointer.execute():
        for page_index, page_layout in _split_reference_origins(layout):
            if page_index != last_page_index:
                if layout_buffer:
                    yield References(
                        page_index=last_page_index,
                        items=layout_buffer,
                    )
                last_page_index = page_index
                layout_buffer = []
            layout_buffer.append(page_layout)

    if layout_buffer:
        yield References(
            page_index=last_page_index,
            items=layout_buffer,
        )


def _split_reference_origins(
    layout: SourceAsset | TextFlowItem,
) -> Generator[tuple[int, SourceAsset | TextFlowItem], None, None]:
    if isinstance(layout, SourceAsset):
        yield layout.page_index, layout
        return

    children: list[SourceTextFragment | SourceAsset] = []
    origin_page_index: int | None = None
    for child in layout.children:
        if not isinstance(child, SourceTextFragment):
            raise ValueError(
                "footnote TextFlowItem cannot contain anchored assets before "
                "flow assembly"
            )
        mark, _ = extract_head_mark(child.content)
        if (
            mark is not None
            and origin_page_index is not None
            and child.page_index != origin_page_index
        ):
            yield origin_page_index, TextFlowItem(
                role=layout.role,
                level=layout.level,
                children=children,
            )
            children = []
            origin_page_index = child.page_index
        elif origin_page_index is None:
            origin_page_index = child.page_index
        children.append(child)

    if origin_page_index is None:
        raise ValueError("TextFlowItem has no source fragments to get page index")
    yield origin_page_index, TextFlowItem(
        role=layout.role,
        level=layout.level,
        children=children,
    )
def _replace_mark_with_reference(references: References, block: SourceTextFragment):
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
