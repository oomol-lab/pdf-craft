from pathlib import Path
from shutil import copy2

from ..sequence import ChapterReader, AssetLayout, ParagraphLayout
from ..sequence.render import render_paragraph_layout


def render_markdown_file(
        chapters_path: Path,
        assets_path: Path,
        output_path: Path,
        output_assets_path: Path,
    ):

    assets_ref_path = output_assets_path
    if not assets_ref_path.is_absolute():
        output_assets_path = output_path.parent / output_assets_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_assets_path.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for chapter in ChapterReader(chapters_path).read():
            if chapter.title is not None:
                f.write("## ")
                for line in render_paragraph_layout(chapter.title):
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
                    for line in render_paragraph_layout(layout):
                        f.write(line)
                f.write("\n\n")


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
        if not latex_content:
            return ""
        if latex_content.startswith("\\[") and latex_content.endswith("\\]"):
            # 块级公式 \[...\] -> $$...$$
            formula = latex_content[2:-2].strip()
            return f"$$\n{formula}\n$$\n"
        elif latex_content.startswith("\\(") and latex_content.endswith("\\)"):
            # 行内公式 \(...\) -> $...$
            formula = latex_content[2:-2].strip()
            return f"${formula}$\n"
        else:
            # 其他情况,假设为块级公式
            return f"$$\n{latex_content}\n$$\n"

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