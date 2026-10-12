# pylint: disable=protected-access

from pathlib import Path
from ...document import PDFCraftExtraction, RenderMode
from ...document.render import materialize_render_view
from ...markdown.render import render_markdown_file
from ...runtime import IO_DOMAIN, worker_context


class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown through one async implementation."""

    async def render(self, extraction: PDFCraftExtraction, output_path: Path,
                     assets_path: Path | None = None, cover_path: Path | None = None,
                     aborted=lambda: False, *, mode: RenderMode = RenderMode.SOURCE,
                     translation_id: str | None = None) -> None:
        async with worker_context(materialize_render_view(
            extraction, mode, translation_id,
        )) as view:
            cover = cover_path
            if cover is None and await IO_DOMAIN.run(view.cover.exists):
                cover = view.cover
            await render_markdown_file(
                view.chapters, view.assets, output_path,
                assets_path or Path("assets"), cover, aborted,
            )
