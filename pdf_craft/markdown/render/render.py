from pathlib import Path
from contextlib import contextmanager
from os.path import relpath
from shutil import copy
from typing import Generator

from ...metering import AbortedCheck, check_aborted
from ...extractor.chapter import (
    Reference,
    create_chapters_reader,
    references_to_map,
    search_references_in_chapter,
)
from .layouts import render_layouts
from ...runtime import IO_DOMAIN, worker_context, worker_iter


async def render_markdown_file(
    chapters_path: Path,
    assets_path: Path,
    output_path: Path,
    output_assets_path: Path,
    cover_path: Path | None,
    aborted: AbortedCheck,
    *, asset_ref_path: Path | None = None,
):
    # ``output_assets_path`` is a destination relative to the Markdown file,
    # unless it is absolute.  Keep the copy destination and emitted Markdown
    # reference separate so neither depends on the process working directory.
    if output_assets_path.is_absolute():
        assets_destination = output_assets_path
    else:
        assets_destination = output_path.parent / output_assets_path
    assets_ref_path = asset_ref_path if asset_ref_path is not None else Path(relpath(assets_destination, output_path.parent))

    await IO_DOMAIN.run(output_path.parent.mkdir, parents=True, exist_ok=True)
    await IO_DOMAIN.run(assets_destination.mkdir, parents=True, exist_ok=True)
    read_chapters = await IO_DOMAIN.run(create_chapters_reader, chapters_path)

    references: list[Reference] = []
    async for chapter in worker_iter(read_chapters()):
        references.extend(search_references_in_chapter(chapter))

    references.sort(key=lambda ref: (ref.page_index, ref.order))
    ref_id_to_number = references_to_map(references)

    async with worker_context(_open_output(output_path)) as f:
        need_blank_line = False
        async for chapter in worker_iter(read_chapters()):
            check_aborted(aborted)

            if need_blank_line:
                need_blank_line = False
                await IO_DOMAIN.run(f.write, "\n\n")

            parts = await IO_DOMAIN.run(lambda chapter=chapter: tuple(render_layouts(
                flow_items=chapter.flow_items,
                assets_path=assets_path,
                output_assets_path=assets_destination,
                asset_ref_path=assets_ref_path,
                toc_level=chapter.level,
                ref_id_to_number=ref_id_to_number,
            )))
            await IO_DOMAIN.run(f.writelines, parts)
            need_blank_line = need_blank_line or bool(parts)

        check_aborted(aborted)
        parts = await IO_DOMAIN.run(lambda: tuple(_render_footnotes_section(
            references=references,
            assets_path=assets_path,
            output_assets_path=assets_destination,
            asset_ref_path=assets_ref_path,
        )))
        await IO_DOMAIN.run(f.writelines, parts)

    if cover_path is not None:
        await IO_DOMAIN.run(copy,
            src=cover_path,
            dst=assets_destination / cover_path.name,
        )


@contextmanager
def _open_output(path: Path):
    with path.open("w", encoding="utf-8") as output:
        yield output


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
            flow_items=ref.flow_items,
            assets_path=assets_path,
            output_assets_path=output_assets_path,
            toc_level=0,
            asset_ref_path=asset_ref_path,
        )
