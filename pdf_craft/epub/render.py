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

from ..markdown.paragraph import flatten, HTMLTag
from ..metering import check_aborted, AbortedCheck
from ..pdf import TITLE_TAGS
from ..sequence import (
    create_chapters_reader,
    search_references_in_chapter,
    references_to_map,
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

    read_chapters = create_chapters_reader(chapters_path)
    references: list[Reference] = []
    for chapter in read_chapters():
        references.extend(search_references_in_chapter(chapter))

    references.sort(key=lambda ref: (ref.page_index, ref.order))
    ref_id_to_number = references_to_map(references)
    get_head: ChapterGetter | None = None
    toc_collection = TocCollection(toc_path)

    for chapter in read_chapters():
        def get_chapter(ch=chapter):
            return _convert_chapter_to_epub(
                chapter=ch,
                assets_path=assets_path,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
        if chapter.id is None:
            get_head = get_chapter
        elif chapter.layouts:
            first_layout = chapter.layouts[0]
            if isinstance(first_layout, ParagraphLayout) and first_layout.ref in TITLE_TAGS:
                title = "".join(_iter_text_in_title(first_layout)).strip()
                if not title:
                    title = "Untitled"
                toc_collection.collect(
                    toc_id=chapter.id,
                    title=title,
                    have_body=len(chapter.layouts) > 1,
                    get_chapter=get_chapter,
                )

    epub_data = EpubData(
        meta=book_meta,
        get_head=get_head,
        chapters=toc_collection.normalize().target,
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

def _iter_text_in_title(title_layout: ParagraphLayout):
    for block in title_layout.blocks:
        for item in flatten(block.content):
            if isinstance(item, str):
                yield item

def _convert_chapter_to_epub(
    chapter: Chapter,
    assets_path: Path,
    inline_latex: bool,
    ref_id_to_number: dict,
) -> ChapterRecord:
    elements = []
    footnotes = []

    for layout in chapter.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(
                asset=layout,
                assets_path=assets_path,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
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
                    kind=TextKind.HEADLINE if layout.ref in TITLE_TAGS else TextKind.BODY,
                    level=layout.level,
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

def _extract_text_from_content(
    content: list[str | InlineExpression | Reference | HTMLTag],
) -> str:
    """Extract plain text from asset content list (for Formula latex_expression)."""
    parts = []
    for item in flatten(content):
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, InlineExpression):
            parts.append(item.content)
    return "".join(parts).strip()

def _convert_asset_to_epub(
    asset: AssetLayout,
    assets_path: Path,
    inline_latex: bool = False,
    ref_id_to_number: dict | None = None,
):
    if asset.ref == "equation":
        latex_expression = _extract_text_from_content(asset.content)
        if not latex_expression:
            return None

        title = _convert_asset_content_to_epub(asset.title, inline_latex, ref_id_to_number)
        caption = _convert_asset_content_to_epub(asset.caption, inline_latex, ref_id_to_number)

        return Formula(
            latex_expression=latex_expression,
            title=title,
            caption=caption,
        )

    elif asset.ref == "image":
        if asset.hash is None:
            return None

        image_file = assets_path / f"{asset.hash}.png"
        if not image_file.exists():
            return None

        title = _convert_asset_content_to_epub(asset.title, inline_latex, ref_id_to_number)
        caption = _convert_asset_content_to_epub(asset.caption, inline_latex, ref_id_to_number)

        return Image(
            path=image_file,
            title=title,
            caption=caption,
        )

    elif asset.ref == "table":
        if asset.hash is None:
            return None

        table_file = assets_path / f"{asset.hash}.png"
        if not table_file.exists():
            return None

        title = _convert_asset_content_to_epub(asset.title, inline_latex, ref_id_to_number)
        caption = _convert_asset_content_to_epub(asset.caption, inline_latex, ref_id_to_number)

        return Image(
            path=table_file,
            title=title,
            caption=caption,
        )

    return None

def _convert_reference_to_footnote_contents(
        ref: Reference,
        assets_path: Path,
        inline_latex: bool,
    ):
    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            asset_element = _convert_asset_to_epub(
                asset=layout,
                assets_path=assets_path,
                inline_latex=inline_latex,
                ref_id_to_number=None,
            )
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
                    level=layout.level,
                    content=content_parts,
                )

def _render_paragraph_layout_with_marks(
        layout: ParagraphLayout,
        inline_latex: bool,
        ref_id_to_number: dict | None = None,
    ) -> list[str | Formula | Mark | EpubHTMLTag]:

    content_parts: list[str | Formula | Mark | EpubHTMLTag] = []
    for block in layout.blocks:
        content_parts.extend(
            _convert_asset_content_to_epub(
                content=block.content,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
        )
    return content_parts


def _convert_asset_content_to_epub(
    content: list[str | InlineExpression | Reference | HTMLTag],
    inline_latex: bool,
    ref_id_to_number: dict | None = None,
) -> list[str | Formula | Mark | EpubHTMLTag]:
    """Convert asset title/caption content to epub_generator format."""
    result: list[str | Formula | Mark | EpubHTMLTag] = []

    def render_content(items: list[str | InlineExpression | Reference | HTMLTag]):
        for item in items:
            if isinstance(item, str):
                yield item

            elif isinstance(item, InlineExpression):
                if inline_latex:
                    yield Formula(latex_expression=item.content.strip())
                else:
                    yield latex_to_plain_text(latex_content=item.content.strip())

            elif ref_id_to_number and isinstance(item, Reference):
                ref_number = ref_id_to_number.get(item.id, 1)
                yield Mark(id=ref_number)

            elif isinstance(item, HTMLTag):
                yield EpubHTMLTag(
                    name=item.definition.name,
                    attributes=item.attributes,
                    content=list(render_content(item.children)),
                )

    result.extend(render_content(content))
    return result
