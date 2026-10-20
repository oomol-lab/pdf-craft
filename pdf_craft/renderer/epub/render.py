from pathlib import Path
from ...runtime import IO_DOMAIN, TEX_DOMAIN, run_cancellable, worker_iter
from ...common import read_xml
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

from ...markdown.paragraph import HTMLTag, flatten
from ...metering import AbortedCheck, check_aborted
from ...extractor.chapter import (
    Chapter, DisplayFormula, InlineExpression, Reference, SourceAsset, decode,
    StandaloneAsset, TextFlowItem,
    create_chapters_reader,
    search_references_in_chapter,
)
from ...extractor.chapter.text_projection import (
    iter_continuous_content,
    iter_rendered_flow_parts,
    iter_run_content,
)
from .anchored import MARKER_CLASS, FloatMarkerRegistry, apply_float_markers, float_side
from .latex_to_text import latex_to_plain_text
from .toc_collection import TocCollection


async def render_epub_file(
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
    read_chapters = await IO_DOMAIN.run(create_chapters_reader, chapters_path)
    float_markers = FloatMarkerRegistry()
    reference_ids: list[tuple[int, int]] = []
    async for chapter in worker_iter(read_chapters()):
        reference_ids.extend(reference.id for reference in search_references_in_chapter(chapter))

    reference_ids.sort()
    ref_id_to_number = {identity: number for number, identity in enumerate(reference_ids, 1)}
    get_head: ChapterGetter | None = None
    toc_collection = await IO_DOMAIN.run(TocCollection, toc_path)

    async for chapter in worker_iter(read_chapters()):

        chapter_file = chapters_path / f"chapter_{chapter.id if chapter.id is not None else 'head'}.xml"
        def get_chapter(path=chapter_file):
            return _convert_chapter_to_epub(
                chapter=decode(read_xml(path)),
                assets_path=assets_path,
                inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
                float_markers=float_markers,
            )

        if chapter.id is None:
            get_head = get_chapter
        elif chapter.flow_items:
            first_layout = chapter.flow_items[0]
            if (
                isinstance(first_layout, TextFlowItem)
                and first_layout.role == "heading"
            ):
                title = "".join(_iter_text_in_title(first_layout)).strip()
                if not title:
                    title = "Untitled"
                have_body = len(chapter.flow_items) > 1
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
    await run_cancellable(
        TEX_DOMAIN,
        lambda cancelled: generate_epub(
            epub_data=epub_data,
            epub_file_path=epub_path,
            lan=lan,
            table_render=table_render,
            latex_render=latex_render,
            assert_not_aborted=lambda: check_aborted(lambda: cancelled() or aborted()),
        ),
        original_aborted=aborted,
    )
    await IO_DOMAIN.run(apply_float_markers, epub_path, float_markers.sides)


def _iter_text_in_title(title_layout: TextFlowItem):
    for item in flatten(iter_continuous_content(title_layout)):
        if isinstance(item, str):
            yield item


class _ReferenceNumbers(dict):
    """Keep definition numbers and occurrence anchors separate within one XHTML."""

    def __init__(self, numbers):
        super().__init__(numbers)
        self.occurrences: dict[int, int] = {}


def _convert_chapter_to_epub(
    chapter: Chapter,
    assets_path: Path,
    inline_latex: bool,
    ref_id_to_number: dict,
    float_markers: FloatMarkerRegistry | None = None,
) -> ChapterRecord:
    elements = []
    footnotes = []
    ref_id_to_number = _ReferenceNumbers(ref_id_to_number)

    for layout in chapter.flow_items:
        if isinstance(layout, (DisplayFormula, StandaloneAsset)):
            asset_element = _convert_asset_to_epub(
                asset=layout.asset, assets_path=assets_path, inline_latex=inline_latex,
                ref_id_to_number=ref_id_to_number,
            )
            if asset_element: elements.append(asset_element)
        elif isinstance(layout, TextFlowItem):
            _append_text_flow_item(
                elements, layout, assets_path, inline_latex, ref_id_to_number, float_markers,
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


def _append_text_flow_item(
    elements,
    text_layout: TextFlowItem,
    assets_path: Path,
    inline_latex: bool,
    ref_id_to_number: dict,
    float_markers: FloatMarkerRegistry | None,
):
    """Render one logical text item, splitting physical EPUB blocks at assets."""
    rendered_assets = {
        id(child): _convert_asset_to_epub(child, assets_path, inline_latex, ref_id_to_number)
        for child in text_layout.children
        if isinstance(child, SourceAsset)
    }
    child_indexes = {id(child): index for index, child in enumerate(text_layout.children)}
    for part in iter_rendered_flow_parts(
        text_layout, lambda asset, assets=rendered_assets: assets[id(asset)] is not None,
    ):
        if isinstance(part, tuple):
            content = list(_transform_content(
                list(iter_run_content(part)), inline_latex, ref_id_to_number,
            ))
            if content:
                elements.append(TextBlock(
                    kind=TextKind.HEADLINE if text_layout.role == "heading" else TextKind.BODY,
                    level=text_layout.level, content=content,
                ))
        else:
            side = float_side(text_layout, child_indexes[id(part)])
            if side is not None and float_markers is not None:
                marker_id = float_markers.new(side)
                elements.append(TextBlock(
                    kind=TextKind.BODY,
                    level=-1,
                    content=[EpubHTMLTag(
                        name="span",
                        attributes=[
                            ("class", MARKER_CLASS),
                            ("data-pdf-craft-float", marker_id),
                        ],
                        )],
                ))
            asset_element = rendered_assets[id(part)]
            assert asset_element is not None
            elements.append(asset_element)


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
    asset: SourceAsset,
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
    if asset.ref == "formula":
        latex_expression = _extract_text_from_content(asset.content)
        if not latex_expression:
            return None

        return Formula(
            latex_expression=latex_expression,
            title=title,
            caption=caption,
        )

    elif asset.ref == "image":
        if asset.asset_hash is None:
            return None

        image_file = assets_path / f"{asset.asset_hash}.png"
        if not image_file.exists():
            return None

        return Image(
            path=image_file,
            title=title,
            caption=caption,
        )

    elif asset.ref == "table":
        if asset.asset_hash is None:
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
            table_file = assets_path / f"{asset.asset_hash}.png"
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
    for layout in ref.flow_items:
        if isinstance(layout, (DisplayFormula, StandaloneAsset)):
            asset_element = _convert_asset_to_epub(
                asset=layout.asset, assets_path=assets_path,
                inline_latex=inline_latex, ref_id_to_number=None,
            )
            if asset_element:
                yield asset_element
            continue
        if isinstance(layout, TextFlowItem):
            rendered_assets = {
                id(child): _convert_asset_to_epub(
                    asset=child,
                    assets_path=assets_path,
                    inline_latex=inline_latex,
                    ref_id_to_number=None,
                )
                for child in layout.children
                if isinstance(child, SourceAsset)
            }
            for part in iter_rendered_flow_parts(
                layout, lambda asset, assets=rendered_assets: assets[id(asset)] is not None,
            ):
                if isinstance(part, tuple):
                    content = list(_transform_content(
                        content=list(iter_run_content(part)),
                        inline_latex=inline_latex,
                        ref_id_to_number=None,
                    ))
                    if content:
                        yield TextBlock(
                            kind=TextKind.BODY,
                            level=layout.level,
                            content=content,
                        )
                else:
                    asset_element = rendered_assets[id(part)]
                    assert asset_element is not None
                    yield asset_element


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
            if isinstance(ref_id_to_number, _ReferenceNumbers):
                occurrence = ref_id_to_number.occurrences.get(ref_number, 0) + 1
                ref_id_to_number.occurrences[ref_number] = occurrence
                if occurrence > 1:
                    # epub-generator's Mark identifies the definition and emits
                    # ref-N. Subsequent occurrences need their own anchor while
                    # linking to the same footnote; the backlink stays on ref-N.
                    yield EpubHTMLTag(
                        name="a", attributes=[
                            ("id", f"ref-{ref_number}-{occurrence}"),
                            ("href", f"#fn-{ref_number}"),
                            ("class", "super"), ("epub:type", "noteref"),
                        ], content=[f"[{ref_number}]"],
                    )
                    continue
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
