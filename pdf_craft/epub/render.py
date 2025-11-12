from pathlib import Path
from typing import Literal
from epub_generator import (
    generate_epub,
    EpubData,
    BookMeta,
    TableRender,
    LaTeXRender,
    Chapter as ChapterRecord,
    ChapterGetter,
    TocItem,
    Text,
    Image,
    Formula,
    Footnote,
    Mark,
    TextKind,
)

from ..common import XMLReader
from ..aborted import check_aborted, AbortedCheck
from ..sequence import (
    decode,
    search_references_in_chapter,
    references_to_map,
    Reference,
    Chapter,
    AssetLayout,
    ParagraphLayout,
)


def render_epub_file(
        chapters_path: Path,
        assets_path: Path,
        epub_path: Path,
        cover_path: Path | None,
        book_meta: BookMeta | None,
        lan: Literal["zh", "en"],
        table_render: TableRender,
        latex_render: LaTeXRender,
        aborted: AbortedCheck,
    ):

    chapters: XMLReader[Chapter] = XMLReader(
        prefix="chapter",
        dir_path=chapters_path,
        decode=decode,
    )
    references: list[Reference] = []
    for chapter in chapters.read():
        references.extend(search_references_in_chapter(chapter))

    references.sort(key=lambda ref: (ref.page_index, ref.order))
    ref_id_to_number = references_to_map(references)
    toc_items: list[TocItem] = []
    get_head: ChapterGetter | None = None

    for chapter in chapters.read():
        def get_chapter(ch=chapter):
            return _convert_chapter_to_epub(
                chapter=ch,
                ref_id_to_number=ref_id_to_number,
                assets_path=assets_path
            )
        if chapter.title is None:
            get_head = get_chapter
        else:
            title = _extract_chapter_title(chapter)
            toc_items.append(TocItem(
                title=title,
                get_chapter=get_chapter
            ))

    epub_data = EpubData(
        meta=book_meta,
        get_head=get_head,
        chapters=toc_items,
        cover_image_path=cover_path,
    )
    check_aborted(aborted)
    generate_epub(
        epub_data=epub_data,
        epub_file_path=epub_path,
        lan=lan,
        table_render=table_render,
        latex_render=latex_render,
    )

def _extract_chapter_title(chapter: Chapter) -> str:
    if chapter.title is not None:
        text_parts = []
        for line in chapter.title.lines:
            for part in line.content:
                if isinstance(part, str):
                    text_parts.append(part)
        return " ".join(text_parts).strip() if text_parts else "Untitled"
    return "Untitled"

def _convert_chapter_to_epub(
    chapter: Chapter,
    ref_id_to_number: dict,
    assets_path: Path
) -> ChapterRecord:
    elements = []
    footnotes = []

    if chapter.title is not None:
        title_content = list(_render_paragraph_with_marks(
            layout=chapter.title,
            ref_id_to_number=ref_id_to_number),
        )
        if title_content:
            elements.append(Text(kind=TextKind.HEADLINE, content=title_content))

    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(layout, assets_path)
            if asset_element:
                elements.append(asset_element)
        elif isinstance(layout, ParagraphLayout):
            paragraph_content = list(_render_paragraph_with_marks(
                layout=layout,
                ref_id_to_number=ref_id_to_number),
            )
            if paragraph_content:
                elements.append(Text(kind=TextKind.BODY, content=paragraph_content))

    chapter_refs = search_references_in_chapter(chapter)
    for ref in chapter_refs:
        footnotes.append(Footnote(
            id=ref_id_to_number.get(ref.id, 1),
            contents=list(_convert_reference_to_footnote_contents(
                ref=ref,
                assets_path=assets_path
            )),
        ))

    return ChapterRecord(elements=elements, footnotes=footnotes)

def _render_paragraph_with_marks(layout: ParagraphLayout, ref_id_to_number: dict):
    for line in layout.lines:
        for part in line.content:
            if isinstance(part, str):
                yield part
            elif isinstance(part, Reference):
                ref_number = ref_id_to_number.get(part.id, 1)
                yield Mark(id=ref_number)

def _convert_asset_to_epub(asset: AssetLayout, assets_path: Path):
    if asset.ref == "equation":
        latex_content = asset.content.strip()
        if not latex_content:
            return None

        if latex_content.startswith("\\[") and latex_content.endswith("\\]"):
            formula = latex_content[2:-2].strip()
        elif latex_content.startswith("\\(") and latex_content.endswith("\\)"):
            formula = latex_content[2:-2].strip()
        else:
            formula = latex_content

        return Formula(latex_expression=formula)

    elif asset.ref == "image":
        if asset.hash is None:
            return None

        image_file = assets_path / f"{asset.hash}.png"
        if not image_file.exists():
            return None

        alt_parts = []
        if asset.title:
            alt_parts.append(asset.title)
        if asset.caption:
            alt_parts.append(asset.caption)
        alt_text = " - ".join(alt_parts) if alt_parts else "image"

        return Image(path=image_file, alt_text=alt_text)

    elif asset.ref == "table":
        if asset.hash is None:
            return None

        table_file = assets_path / f"{asset.hash}.png"
        if not table_file.exists():
            return None

        alt_parts = []
        if asset.title:
            alt_parts.append(asset.title)
        if asset.caption:
            alt_parts.append(asset.caption)
        alt_text = " - ".join(alt_parts) if alt_parts else "table"

        return Image(path=table_file, alt_text=alt_text)

    return None

def _convert_reference_to_footnote_contents(ref: Reference, assets_path: Path):
    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(layout, assets_path)
            if asset_element:
                yield asset_element
        elif isinstance(layout, ParagraphLayout):
            content_parts: list[str | Mark] = []
            for line in layout.lines:
                for part in line.content:
                    if isinstance(part, str):
                        content_parts.append(part)

            if content_parts:
                yield Text(kind=TextKind.BODY, content=content_parts)