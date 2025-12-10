from pathlib import Path
from typing import Generator

from ..common import XMLReader
from ..metering import check_aborted, AbortedCheck
from ..sequence import (
    decode,
    search_references_in_chapter,
    references_to_map,
    Reference,
    Chapter,
    AssetLayout,
    ParagraphLayout,
)

from .layouts import render_layouts, render_paragraph


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
        need_blank_line = False
        for chapter in chapters.read():
            check_aborted(aborted)

            if need_blank_line:
                need_blank_line = False
                f.write("\n\n")

            if chapter.title is not None:
                f.write("## ")
                for line in render_paragraph(
                    paragraph=chapter.title,
                    ref_id_to_number=ref_id_to_number,
                ):
                    f.write(line)
                f.write("\n\n")

            for part in render_layouts(
                layouts=chapter.layouts,
                assets_path=assets_path,
                output_assets_path=output_assets_path,
                asset_ref_path=assets_ref_path,
            ):
                f.write(part)
                need_blank_line = True

        check_aborted(aborted)
        for part in _render_footnotes_section(references):
            f.write(part)

def _render_footnotes_section(references: list[Reference]) -> Generator[str, None, None]:
    if not references:
        return
    yield "\n\n---\n\n## References\n\n"
    for i, ref in enumerate(references, 1):
        yield f"[^{i}]: "
        yield from _render_reference_content(ref)
        yield "\n\n"

def _render_reference_content(ref: Reference):
    first = True
    for layout in ref.layouts:
        if isinstance(layout, AssetLayout):
            if layout.content:
                if not first:
                    yield " "
                yield layout.content.strip()
                first = False
        elif isinstance(layout, ParagraphLayout):
            for line in layout.lines:
                for part in line.content:
                    if isinstance(part, str):
                        if not first:
                            yield " "
                        yield part
                        first = False
