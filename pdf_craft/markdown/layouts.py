from pathlib import Path
from shutil import copy2
from typing import Iterable, Generator

from ..expression import to_markdown_string, ExpressionKind
from ..sequence import (
    is_chinese_char,
    Reference,
    InlineExpression,
    AssetLayout,
    ParagraphLayout,
)


RefIdMap = dict[tuple[int, int], int]


def render_layouts(
        layouts: Iterable[ParagraphLayout | AssetLayout],
        assets_path: Path,
        output_assets_path: Path,
        asset_ref_path: Path,
        ref_id_to_number: RefIdMap | None = None,
    ) -> Generator[str, None, None]:

    is_first_layout = True
    for layout in layouts:
        if is_first_layout:
            is_first_layout = False
        else:
            yield "\n\n"
        if isinstance(layout, AssetLayout):
            asset_markdown = _render_asset(
                asset=layout,
                assets_path=assets_path,
                output_assets_path=output_assets_path,
                asset_ref_path=asset_ref_path,
            )
            if asset_markdown:
                yield asset_markdown
        elif isinstance(layout, ParagraphLayout):
            yield from render_paragraph(
                paragraph=layout,
                ref_id_to_number=ref_id_to_number,
            )

def render_paragraph(paragraph: ParagraphLayout, ref_id_to_number: RefIdMap | None = None) -> Generator[str, None, None]:
    yield from _normalize_paragraph(
        parts=(part for line in paragraph.blocks for part in _content_to_text_parts(
            content=line.content,
            ref_id_to_number=ref_id_to_number,
        )),
    )

def _render_asset(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
) -> str:

    if asset.ref == "equation":
        latex_content = asset.content.strip()
        if latex_content:
            latex_content = to_markdown_string(
                kind=ExpressionKind.DISPLAY_BRACKET,
                content=latex_content,
            )
        return latex_content

    elif asset.ref in ("image", "table"):
        alt_parts: list[str] = []
        if asset.title:
            alt_parts.append(asset.title)
        if asset.caption:
            alt_parts.append(asset.caption)
        alt_text = " - ".join(alt_parts) if alt_parts else ""

        if asset.hash is None:
            return ""

        source_file = assets_path / f"{asset.hash}.png"
        if not source_file.exists():
            return ""

        target_file = output_assets_path / f"{asset.hash}.png"
        if not target_file.exists():
            copy2(source_file, target_file)

        if asset_ref_path.is_absolute():
            image_path = target_file
        else:
            image_path = asset_ref_path / f"{asset.hash}.png"

        # 使用 POSIX 风格路径(markdown 标准)
        image_path_str = str(image_path).replace("\\", "/")

        return f"![{alt_text}]({image_path_str})\n"

    return ""

def _content_to_text_parts(
        content: Iterable[str | InlineExpression | Reference],
        ref_id_to_number: RefIdMap | None,
    ) -> Generator[str, None, None]:

    for part in content:
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
            yield f"[^{ref_number}]"


def _normalize_paragraph(parts: Iterable[str]) -> Generator[str, None, None]:
    last_char: str | None = None
    is_line_head = True
    for part in _split_enters(parts):
        if part != "\n":
            if is_line_head:
                is_line_head = False
                part = part.lstrip()
                if last_char is not None and (
                    not is_chinese_char(last_char) or \
                    not is_chinese_char(part[0])
                ):
                    yield " "
            if part:
                yield part
                part = part.rstrip()
                if part:
                    last_char = part[-1]
        else:
            is_line_head = True


def _split_enters(parts: Iterable[str]) -> Generator[str, None, None]:
    for part in parts:
        if not part:
            continue
        split_parts = part.splitlines()
        yield split_parts[0]
        for i in range(1, len(split_parts)):
            yield "\n"
            yield split_parts[i]
