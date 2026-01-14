from pathlib import Path
from typing import Generator, Literal

from epub_generator import (
    BookMeta,
    ChapterGetter,
    EpubData,
    Footnote,
    Formula,
    Image,
    LaTeXRender,
    Mark,
    Table,
    TableRender,
    TextBlock,
    TextKind,
    generate_epub,
)
from epub_generator import (
    Chapter as ChapterRecord,
)
from epub_generator import (
    HTMLTag as EpubHTMLTag,
)

from ..markdown.paragraph import HTMLTag, flatten
from ..metering import AbortedCheck, check_aborted
from ..pdf import TITLE_TAGS
from ..sequence import (
    AssetLayout,
    Chapter,
    InlineExpression,
    ParagraphLayout,
    Reference,
    create_chapters_reader,
    references_to_map,
    search_references_in_chapter,
)
from .latex_to_text import latex_to_plain_text
from .toc_collection import TocCollection


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
            if (
                isinstance(first_layout, ParagraphLayout)
                and first_layout.ref in TITLE_TAGS
            ):
                title = "".join(_iter_text_in_title(first_layout)).strip()
                if not title:
                    title = "Untitled"
                have_body = len(chapter.layouts) > 1
                toc_collection.collect(
                    toc_id=chapter.id,
                    title=title,
                    have_body=have_body,
                    get_chapter=get_chapter if have_body else None,
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
            content: list[str | Formula | Mark | EpubHTMLTag] = []
            for block in layout.blocks:
                content.extend(
                    _transform_content(
                        content=block.content,
                        inline_latex=inline_latex,
                        ref_id_to_number=None,
                    )
                )
            if content:
                elements.append(
                    TextBlock(
                        kind=TextKind.HEADLINE
                        if layout.ref in TITLE_TAGS
                        else TextKind.BODY,
                        level=layout.level,
                        content=content,
                    )
                )

    chapter_refs = search_references_in_chapter(chapter)
    for ref in chapter_refs:
        footnotes.append(
            Footnote(
                id=ref_id_to_number.get(ref.id, 1),
                contents=list(
                    _convert_reference_to_footnote_contents(
                        ref=ref, inline_latex=inline_latex, assets_path=assets_path
                    )
                ),
            )
        )

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
    title = list(
        _transform_content(
            content=asset.title,
            inline_latex=inline_latex,
            ref_id_to_number=ref_id_to_number,
        )
    )
    caption = list(
        _transform_content(
            content=asset.caption,
            inline_latex=inline_latex,
            ref_id_to_number=ref_id_to_number,
        )
    )
    if asset.ref == "equation":
        latex_expression = _extract_text_from_content(asset.content)
        if not latex_expression:
            return None

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

        return Image(
            path=image_file,
            title=title,
            caption=caption,
        )

    elif asset.ref == "table":
        if asset.hash is None:
            return None

        html_content: EpubHTMLTag | None = None
        for item in _transform_content(
            content=asset.content,
            inline_latex=inline_latex,
            ref_id_to_number=ref_id_to_number,
        ):
            if isinstance(item, EpubHTMLTag):
                html_content = item
                break

        if html_content is None:
            table_file = assets_path / f"{asset.hash}.png"
            if not table_file.exists():
                return None
            return Image(
                path=table_file,
                title=title,
                caption=caption,
            )
        else:
            return Table(
                title=title,
                caption=caption,
                html_content=html_content,
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
            content: list[str | Formula | Mark | EpubHTMLTag] = []
            for block in layout.blocks:
                content.extend(
                    _transform_content(
                        content=block.content,
                        inline_latex=inline_latex,
                        ref_id_to_number=None,
                    )
                )
            if content:
                yield TextBlock(
                    kind=TextKind.BODY,
                    level=layout.level,
                    content=content,
                )


def _transform_content(
    content: list[str | InlineExpression | Reference | HTMLTag],
    inline_latex: bool,
    ref_id_to_number: dict | None = None,
) -> Generator[str | Formula | Mark | EpubHTMLTag, None, None]:
    for item in content:
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
                content=list(
                    _transform_content(
                        content=item.children,
                        inline_latex=inline_latex,
                        ref_id_to_number=ref_id_to_number,
                    )
                ),
            )
