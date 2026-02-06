from pathlib import Path
from shutil import copy2
from typing import Callable, Generator, Iterable

from ...expression import ExpressionKind, to_markdown_string
from ...pdf import TITLE_TAGS
from ...sequence import (
    AssetLayout,
    BlockMember,
    InlineExpression,
    ParagraphLayout,
    Reference,
    RefIdMap,
)
from ..paragraph import render_markdown_paragraph
from .table import render_table_content

_MAX_TOC_LEVELS = 3
_MAX_TITLE_LEVELS = 6


def render_layouts(
    layouts: Iterable[ParagraphLayout | AssetLayout],
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    toc_level: int,
    ref_id_to_number: RefIdMap | None = None,
) -> Generator[str, None, None]:
    is_first_layout = True
    toc_level = min(toc_level, _MAX_TOC_LEVELS - 1)

    for layout in layouts:
        if is_first_layout:
            is_first_layout = False
        else:
            yield "\n\n"
        if isinstance(layout, AssetLayout):
            yield from _render_asset(
                asset=layout,
                assets_path=assets_path,
                output_assets_path=output_assets_path,
                asset_ref_path=asset_ref_path,
                ref_id_to_number=ref_id_to_number,
            )
        elif isinstance(layout, ParagraphLayout):
            yield from render_paragraph(
                paragraph=layout,
                toc_level=toc_level,
                ref_id_to_number=ref_id_to_number,
            )


def render_paragraph(
    paragraph: ParagraphLayout, toc_level: int, ref_id_to_number: RefIdMap | None = None
) -> Generator[str, None, None]:
    if paragraph.level >= 0 and paragraph.ref in TITLE_TAGS:
        level = min(toc_level + paragraph.level, _MAX_TITLE_LEVELS)
        for _ in range(level + 1):  # level 0 对应 1 个 #
            yield "#"
        yield " "

    def render_member(part: BlockMember | str) -> Generator[str, None, None]:
        if isinstance(part, str):
            yield to_markdown_string(
                kind=ExpressionKind.TEXT,
                content=part,
            )
        elif isinstance(part, InlineExpression):
            latex_content = part.content.strip()
            if latex_content:
                yield to_markdown_string(
                    kind=part.kind,
                    content=latex_content,
                )
        elif ref_id_to_number and isinstance(part, Reference):
            ref_number = ref_id_to_number.get(part.id, 1)
            yield "[^"
            yield str(ref_number)
            yield "]"

    for block in paragraph.blocks:
        yield from render_markdown_paragraph(
            children=block.content,
            render_payload=render_member,
        )


_MemberRender = Callable[[BlockMember | str], Iterable[str]]


def _render_asset(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    ref_id_to_number: RefIdMap | None = None,
) -> Generator[str, None, None]:
    def render_member(part: BlockMember | str) -> Generator[str, None, None]:
        if isinstance(part, str):
            yield to_markdown_string(
                kind=ExpressionKind.TEXT,
                content=part,
            )
        elif isinstance(part, InlineExpression):
            latex_content = part.content.strip()
            if latex_content:
                yield to_markdown_string(
                    kind=part.kind,
                    content=latex_content,
                )
        elif ref_id_to_number and isinstance(part, Reference):
            ref_number = ref_id_to_number.get(part.id, 1)
            yield "[^"
            yield str(ref_number)
            yield "]"

    has_content = False

    if asset.title:
        title_str = "".join(
            render_markdown_paragraph(
                children=asset.title,
                render_payload=render_member,
            )
        ).strip()
        if title_str:
            yield title_str
            has_content = True

    yield from _render_asset_content(
        asset=asset,
        assets_path=assets_path,
        output_assets_path=output_assets_path,
        asset_ref_path=asset_ref_path,
        render_member=render_member,
        has_content_before=has_content,
    )
    if asset.ref in ("equation", "table"):
        if asset.content:
            has_content = True
    elif asset.ref == "image":
        if asset.hash:
            has_content = True

    if asset.caption:
        caption_str = "".join(
            render_markdown_paragraph(
                children=asset.caption,
                render_payload=render_member,
            )
        ).strip()
        if caption_str:
            if has_content:
                yield "\n\n"
            yield caption_str


def _render_asset_content(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    render_member: _MemberRender,
    has_content_before: bool,
) -> Generator[str, None, None]:
    if asset.ref == "equation":
        content_str = "".join(
            render_markdown_paragraph(
                children=asset.content,
                render_payload=render_member,
            )
        )
        latex_content = content_str.strip()
        if latex_content:
            if has_content_before:
                yield "\n\n"
            yield to_markdown_string(
                kind=ExpressionKind.DISPLAY_BRACKET,
                content=latex_content,
            )

    elif asset.ref == "table":
        if asset.content:
            if has_content_before:
                yield "\n\n"
            yield render_table_content(
                html_string="".join(
                    render_markdown_paragraph(
                        children=asset.content,
                        render_payload=render_member,
                    )
                )
            )

    elif asset.ref == "image":
        yield from _render_image(
            asset=asset,
            assets_path=assets_path,
            output_assets_path=output_assets_path,
            asset_ref_path=asset_ref_path,
            has_content_before=has_content_before,
        )


def _render_image(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    has_content_before: bool,
) -> Generator[str, None, None]:
    # 渲染图片
    if asset.hash is None:
        return

    source_file = assets_path / f"{asset.hash}.png"
    if not source_file.exists():
        return

    target_file = output_assets_path / f"{asset.hash}.png"
    if not target_file.exists():
        copy2(source_file, target_file)

    if asset_ref_path.is_absolute():
        image_path = target_file
    else:
        image_path = asset_ref_path / f"{asset.hash}.png"

    # 使用 POSIX 风格路径(markdown 标准)
    image_path_str = str(image_path).replace("\\", "/")

    # 图片的 alt 保持空
    if has_content_before:
        yield "\n\n"
    yield f"![]({image_path_str})"
