from pathlib import Path
from shutil import copy
from typing import Generator

from ...metering import AbortedCheck, check_aborted
from ...sequence import (
    Reference,
    create_chapters_reader,
    references_to_map,
    search_references_in_chapter,
)
from .layouts import render_layouts


def render_markdown_file(
    chapters_path: Path,
    assets_path: Path,
    output_path: Path,
    output_assets_path: Path,
    cover_path: Path | None,
    aborted: AbortedCheck,
):
    assets_ref_path = output_assets_path
    if not assets_ref_path.is_absolute():
        output_assets_path = output_path.parent / output_assets_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_assets_path.mkdir(parents=True, exist_ok=True)
    read_chapters = create_chapters_reader(chapters_path)

    references: list[Reference] = []
    for chapter in read_chapters():
        references.extend(search_references_in_chapter(chapter))

    references.sort(key=lambda ref: (ref.page_index, ref.order))
    ref_id_to_number = references_to_map(references)

    with open(output_path, "w", encoding="utf-8") as f:
        need_blank_line = False
        for chapter in read_chapters():
            check_aborted(aborted)

            if need_blank_line:
                need_blank_line = False
                f.write("\n\n")

            for part in render_layouts(
                layouts=chapter.layouts,
                assets_path=assets_path,
                output_assets_path=output_assets_path,
                asset_ref_path=assets_ref_path,
                toc_level=chapter.level,
                ref_id_to_number=ref_id_to_number,
            ):
                f.write(part)
                need_blank_line = True

        check_aborted(aborted)
        for part in _render_footnotes_section(
            references=references,
            assets_path=assets_path,
            output_assets_path=output_assets_path,
            asset_ref_path=assets_ref_path,
        ):
            f.write(part)

    if cover_path is not None:
        copy(
            src=cover_path,
            dst=output_assets_path / cover_path.name,
        )


def _render_footnotes_section(
    references: list[Reference],
    assets_path: Path,
    output_assets_path: Path,
    asset_ref_path: Path,
) -> Generator[str, None, None]:
    if not references:
        return
    yield "\n\n---\n\n## References"
    for i, ref in enumerate(references, 1):
        yield "\n\n"
        yield f"[^{i}]:  "
        yield from render_layouts(
            layouts=ref.layouts,
            assets_path=assets_path,
            output_assets_path=output_assets_path,
            toc_level=0,
            asset_ref_path=asset_ref_path,
        )
