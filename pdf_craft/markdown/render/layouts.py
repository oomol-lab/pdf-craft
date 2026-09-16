from pathlib import Path
from shutil import copy2
from typing import Callable, Generator, Iterable

from ...expression import ExpressionKind, to_markdown_string
from ...extractor.chapter import (
    BlockMember, DisplayFormula, FlowItem, InlineExpression, Reference,
    RefIdMap, SourceAsset, StandaloneAsset, TextFlowItem,
)
from ...extractor.chapter.text_projection import (
    iter_continuous_content,
    iter_rendered_flow_parts,
)
from ..paragraph import render_markdown_paragraph
from .table import render_table_content

_MAX_TOC_LEVELS = 3
_MAX_TITLE_LEVELS = 6


def render_layouts(
    flow_items: Iterable[FlowItem],
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    toc_level: int,
    ref_id_to_number: RefIdMap | None = None,
) -> Generator[str, None, None]:
    is_first_layout = True
    toc_level = min(toc_level, _MAX_TOC_LEVELS - 1)

    for layout in flow_items:
        if is_first_layout:
            is_first_layout = False
        else:
            yield "\n\n"
        if isinstance(layout, (DisplayFormula, StandaloneAsset)):
            yield from _render_asset(layout.asset, assets_path, output_assets_path, asset_ref_path, ref_id_to_number)
        elif isinstance(layout, TextFlowItem):
            rendered_assets = {
                id(child): tuple(_render_asset(
                    child, assets_path, output_assets_path, asset_ref_path, ref_id_to_number,
                ))
                for child in layout.children
                if isinstance(child, SourceAsset)
            }
            emitted_part = False
            for part in iter_rendered_flow_parts(
                layout, lambda asset, assets=rendered_assets: bool(assets[id(asset)]),
            ):
                if emitted_part:
                    yield "\n\n"
                emitted_part = True
                if isinstance(part, tuple):
                    yield from render_paragraph(
                        TextFlowItem(layout.role, layout.level, list(part)),
                        toc_level,
                        ref_id_to_number,
                    )
                else:
                    yield from rendered_assets[id(part)]


def render_paragraph(
    paragraph: TextFlowItem, toc_level: int, ref_id_to_number: RefIdMap | None = None
) -> Generator[str, None, None]:
    if paragraph.level >= 0 and paragraph.role == "heading":
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

    yield from render_markdown_paragraph(
        children=list(iter_continuous_content(paragraph)),
        render_payload=render_member,
    )


_MemberRender = Callable[[BlockMember | str], Iterable[str]]


def _render_asset(
    asset: SourceAsset,
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
    if asset.ref in ("formula", "table"):
        if asset.content:
            has_content = True
    elif asset.ref == "image":
        if asset.asset_hash:
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
    asset: SourceAsset,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    render_member: _MemberRender,
    has_content_before: bool,
) -> Generator[str, None, None]:
    if asset.ref == "formula":
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
    asset: SourceAsset,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
    has_content_before: bool,
) -> Generator[str, None, None]:
    # 渲染图片
    if asset.asset_hash is None:
        return

    source_file = assets_path / f"{asset.asset_hash}.png"
    if not source_file.exists():
        return

    target_file = output_assets_path / f"{asset.asset_hash}.png"
    if not target_file.exists():
        copy2(source_file, target_file)

    if asset_ref_path.is_absolute():
        image_path = target_file
    else:
        image_path = asset_ref_path / f"{asset.asset_hash}.png"

    # 使用 POSIX 风格路径(markdown 标准)
    image_path_str = str(image_path).replace("\\", "/")

    # 图片的 alt 保持空
    if has_content_before:
        yield "\n\n"
    yield f"![]({image_path_str})"
