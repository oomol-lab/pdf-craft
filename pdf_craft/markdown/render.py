from pathlib import Path
from shutil import copy2

from ..sequence import ChapterReader, AssetLayout, ParagraphLayout
from ..sequence.render import render_paragraph_layout


def render_markdown_file(
        chapters_path: Path,
        assets_path: Path, # 原始资源文件夹地址
        output_path: Path, # 写入的 Markdown 文件
        output_assets_path: Path, # 写入的资源文件夹。如果写绝对路径,在 markdown 中会写绝对路径引用。如果写相对路径,会相对 output_path,并在 markdown 中相对引用。
    ):
    # 确保输出资源文件夹存在
    output_assets_path.mkdir(parents=True, exist_ok=True)

    # 确保输出文件的父目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 计算资源在 markdown 中的引用路径
    if output_assets_path.is_absolute():
        # 绝对路径
        asset_ref_path = output_assets_path
    else:
        # 相对路径 - 相对于 output_path
        asset_ref_path = output_assets_path

    # 流式写入 markdown 文件
    with open(output_path, "w", encoding="utf-8") as f:
        for chapter in ChapterReader(chapters_path).read():
            # 处理章节标题
            if chapter.title is not None:
                f.write("## ")
                for line in render_paragraph_layout(chapter.title):
                    f.write(line)
                f.write("\n\n")

            # 处理章节内容
            for layout in chapter.layouts:
                if isinstance(layout, AssetLayout):
                    # 处理资源(图片、表格、公式)
                    asset_markdown = _render_asset(layout, assets_path, output_assets_path, asset_ref_path)
                    if asset_markdown:
                        f.write(asset_markdown)
                elif isinstance(layout, ParagraphLayout):
                    # 处理段落
                    for line in render_paragraph_layout(layout):
                        f.write(line)
                    f.write("\n\n")


def _render_asset(
    asset: AssetLayout,
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
) -> str:
    """渲染资源为 markdown 格式"""

    # 构建 alt 文本: title + caption
    alt_parts = []
    if asset.title:
        alt_parts.append(asset.title)
    if asset.caption:
        alt_parts.append(asset.caption)
    alt_text = " - ".join(alt_parts) if alt_parts else ""

    if asset.ref == "equation":
        # 公式用 LaTeX 处理
        latex_content = asset.content.strip()
        if not latex_content:
            return ""

        # 处理不同的 LaTeX 格式
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
        # 图片和表格都作为图片处理
        if asset.hash is None:
            return ""

        # 复制资源文件
        source_file = assets_path / f"{asset.hash}.png"
        if not source_file.exists():
            return ""

        target_file = output_assets_path / f"{asset.hash}.png"
        if not target_file.exists():
            copy2(source_file, target_file)

        # 生成 markdown 图片引用
        if asset_ref_path.is_absolute():
            image_path = target_file
        else:
            image_path = asset_ref_path / f"{asset.hash}.png"

        # 使用 POSIX 风格路径(markdown 标准)
        image_path_str = str(image_path).replace("\\", "/")

        return f"![{alt_text}]({image_path_str})\n"

    return ""