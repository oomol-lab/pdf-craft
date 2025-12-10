from pathlib import Path
from shutil import copy2
from typing import Generator

from ..common import XMLReader
from ..expression import to_markdown_string, ExpressionKind
from ..metering import check_aborted, AbortedCheck
from ..sequence import (
    decode,
    is_chinese_char,
    search_references_in_chapter,
    references_to_map,
    Reference,
    InlineExpression,
    Chapter,
    AssetLayout,
    ParagraphLayout,
)

from .footnotes import render_footnotes_section


def render_markdown_file(
        chapters_path: Path,
        assets_path: Path,
        output_path: Path,
        output_assets_path: Path,
        aborted: AbortedCheck,
    ):

    assets_ref_path = output_assets_path
    if not assets_ref_path.is_absolute():
        output_assets_path = output_path.parent / output_assets_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_assets_path.mkdir(parents=True, exist_ok=True)
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

    with open(output_path, "w", encoding="utf-8") as f:
        for chapter in chapters.read():
            check_aborted(aborted)
            if chapter.title is not None:
                f.write("## ")
                for line in _render_paragraph_layout(chapter.title, ref_id_to_number):
                    f.write(line)
                f.write("\n\n")
            for layout in chapter.layouts:
                if isinstance(layout, AssetLayout):
                    asset_markdown = _render_asset(
                        asset=layout,
                        assets_path=assets_path,
                        output_assets_path=output_assets_path,
                        asset_ref_path=assets_ref_path,
                    )
                    if asset_markdown:
                        f.write(asset_markdown)
                elif isinstance(layout, ParagraphLayout):
                    for line in _render_paragraph_layout(layout, ref_id_to_number):
                        f.write(line)
                f.write("\n\n")

        check_aborted(aborted)
        for part in render_footnotes_section(references):
            f.write(part)

def _render_paragraph_layout(layout: ParagraphLayout, ref_id_to_number: dict) -> Generator[str, None, None]:
    last_char: str | None = None
    for line in _normalize_lines(layout, ref_id_to_number):
        if last_char is not None and (
            not is_chinese_char(last_char) or \
            not is_chinese_char(line[0])
        ):
            yield " "
        last_char = line[-1] if line else None
        yield line

def _normalize_lines(layout: ParagraphLayout, ref_id_to_number: dict) -> Generator[str, None, None]:
    for line_layout in layout.lines:
        line_content = _render_line_content(line_layout.content, ref_id_to_number)
        if line_content:
            for line in line_content.splitlines():
                if line:
                    yield line

def _render_line_content(content: list[str | InlineExpression | Reference], ref_id_to_number: dict) -> str:
    result = []
    for part in content:
        if isinstance(part, str):
            result.append(to_markdown_string(
                kind=ExpressionKind.TEXT,
                content=part,
            ))
        elif isinstance(part, InlineExpression):
            latex_content = part.content.strip()
            if latex_content:
                result.append(to_markdown_string(
                    kind=part.kind,
                    content=latex_content,
                ))
        elif isinstance(part, Reference):
            ref_number = ref_id_to_number.get(part.id, 1)
            result.append(f"[^{ref_number}]")
        else:
            result.append(str(part))
    return "".join(result)

def _render_asset(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
) -> str:

    alt_parts = []
    if asset.title:
        alt_parts.append(asset.title)
    if asset.caption:
        alt_parts.append(asset.caption)
    alt_text = " - ".join(alt_parts) if alt_parts else ""

    if asset.ref == "equation":
        latex_content = asset.content.strip()
        if latex_content:
            latex_content = to_markdown_string(
                kind=ExpressionKind.DISPLAY_BRACKET,
                content=latex_content,
            )
            latex_content = f"\n{latex_content}\n"
        return latex_content

    elif asset.ref in ("image", "table"):
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
