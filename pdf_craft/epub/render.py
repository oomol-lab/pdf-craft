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
    TextBlock,
    Image,
    Formula,
    Footnote,
    Mark,
    TextKind,
    HTMLTag as EpubHTMLTag,
)

from .toc_collection import TocCollection
from .latex_to_text import latex_to_plain_text

from ..common import XMLReader
from ..markdown.paragraph import HTMLTag
from ..metering import check_aborted, AbortedCheck
from ..sequence import (
    decode,
    search_references_in_chapter,
    references_to_map,
    Content,
    InlineExpression,
    Reference,
    Chapter,
    AssetLayout,
    ParagraphLayout,
)


def render_epub_file(
        chapters_path: Path,
        toc_path: Path | None,
        assets_path: Path,
        epub_path: Path,
        cover_path: Path | None,
        book_meta: BookMeta | None,
        lan: Literal["zh", "en"],
        table_render: TableRender,
        latex_render: LaTeXRender,
        inline_latex: bool,
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
    get_head: ChapterGetter | None = None
    toc_collection = TocCollection(toc_path)

    for i, chapter in enumerate(chapters.read()):
        def get_chapter(ch=chapter):
            return _convert_chapter_to_epub(
                chapter=ch,
                assets_path=assets_path,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
        if chapter.title is None:
            get_head = get_chapter
        else:
            toc_collection.collect(
                id=i + 1,
                title=_extract_chapter_title(chapter),
                get_chapter=get_chapter,
            )

    epub_data = EpubData(
        meta=book_meta,
        get_head=get_head,
        chapters=toc_collection.target,
        cover_image_path=cover_path,
    )
    check_aborted(aborted)
    generate_epub(
        epub_data=epub_data,
        epub_file_path=epub_path,
        lan=lan,
        table_render=table_render,
        latex_render=latex_render,
        assert_not_aborted=lambda: check_aborted(aborted),
    )

def _extract_chapter_title(chapter: Chapter) -> str:
    if chapter.title is not None:
        text_parts = []
        for line in chapter.title.blocks:
            for part in line.content:
                if isinstance(part, str):
                    text_parts.append(part)
        return " ".join(text_parts).strip() if text_parts else "Untitled"
    return "Untitled"

def _convert_chapter_to_epub(
    chapter: Chapter,
    assets_path: Path,
    inline_latex: bool,
    ref_id_to_number: dict,
) -> ChapterRecord:
    elements = []
    footnotes = []

    if chapter.title is not None:
        title_content = _render_paragraph_layout_with_marks(
            layout=chapter.title,
            inline_latex=inline_latex,
            ref_id_to_number=ref_id_to_number,
        )
        if title_content:
            elements.append(TextBlock(
                kind=TextKind.HEADLINE,
                content=title_content,
            ))

    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(layout, assets_path)
            if asset_element:
                elements.append(asset_element)
        elif isinstance(layout, ParagraphLayout):
            paragraph_content = _render_paragraph_layout_with_marks(
                layout=layout,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
            if paragraph_content:
                elements.append(TextBlock(
                    kind=TextKind.BODY,
                    content=paragraph_content,
                ))

    chapter_refs = search_references_in_chapter(chapter)
    for ref in chapter_refs:
        footnotes.append(Footnote(
            id=ref_id_to_number.get(ref.id, 1),
            contents=list(_convert_reference_to_footnote_contents(
                ref=ref,
                inline_latex=inline_latex,
                assets_path=assets_path
            )),
        ))

    return ChapterRecord(elements=elements, footnotes=footnotes)

def _convert_asset_to_epub(asset: AssetLayout, assets_path: Path):
    if asset.ref == "equation":
        latex_expression = asset.content.strip()
        if not latex_expression:
            return None
        return Formula(latex_expression)

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

def _convert_reference_to_footnote_contents(
        ref: Reference,
        assets_path: Path,
        inline_latex: bool,
    ):
    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(layout, assets_path)
            if asset_element:
                yield asset_element
        elif isinstance(layout, ParagraphLayout):
            content_parts = _render_paragraph_layout_with_marks(
                layout=layout,
                inline_latex=inline_latex,
            )
            if content_parts:
                yield TextBlock(
                    kind=TextKind.BODY,
                    content=content_parts,
                )

def _render_paragraph_layout_with_marks(
        layout: ParagraphLayout,
        inline_latex: bool,
        ref_id_to_number: dict | None = None,
    ) -> list[str | Formula | Mark | EpubHTMLTag]:

    def render_content(content: Content):
        for part in content:
            if isinstance(part, str):
                yield part

            elif isinstance(part, InlineExpression):
                if inline_latex:
                    yield Formula(latex_expression=part.content.strip())
                else:
                    yield latex_to_plain_text(
                        latex_content=part.content.strip(),
                    )
            elif ref_id_to_number and isinstance(part, Reference):
                ref_number = ref_id_to_number.get(part.id, 1)
                yield Mark(id=ref_number)

            elif isinstance(part, HTMLTag):
                yield EpubHTMLTag(
                    name=part.definition.name,
                    attributes=part.attributes,
                    content=list(render_content(part.children)),
                )

    content_parts: list[str | Formula | Mark | EpubHTMLTag] = []
    for block in layout.blocks:
        content_parts.extend(render_content(block.content))
    return content_parts
