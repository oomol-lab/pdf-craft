# pylint: disable=protected-access

from pathlib import Path
from os.path import relpath
from ...document import PDFCraftExtraction, RenderMode
from ...document.render import materialize_render_view
from ...document.content import consume_content, read_content
from ...markdown.render import render_markdown_file
from ...runtime import IO_DOMAIN, worker_context, temporary_directory, run_atomic_cancellable
from ..output import publish


class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown through one async implementation."""

    async def render(self, extraction: PDFCraftExtraction, output_path: Path,
                     assets_path: Path | None = None, cover_path: Path | None = None,
                     aborted=lambda: False, *, mode: RenderMode = RenderMode.SOURCE,
                     translation_id: str | None = None, on_body=None, on_footnote=None) -> None:
        async with worker_context(materialize_render_view(
            extraction, mode, translation_id,
        )) as view:
            await consume_content(read_content(view.chapters), on_body=on_body, on_footnote=on_footnote, aborted=aborted)
            cover = cover_path
            if cover is None and await IO_DOMAIN.run(view.cover.exists):
                cover = view.cover
            destination = assets_path or Path("assets")
            if not destination.is_absolute():
                destination = output_path.parent / destination
            async with temporary_directory("pdf-craft-markdown-") as directory:
                staged = Path(directory)
                await render_markdown_file(
                    view.chapters, view.assets, staged / output_path.name,
                    staged / "assets", cover, aborted,
                    asset_ref_path=Path(relpath(destination, output_path.parent)),
                )
                from ...metering import check_aborted
                check_aborted(aborted)
                def commit(bridge):
                    resources = [(path, destination / path.relative_to(staged / "assets"))
                                 for path in (staged / "assets").rglob("*") if path.is_file()]
                    publish([*resources, (staged / output_path.name, output_path)], bridge.commit)
                await run_atomic_cancellable(IO_DOMAIN, commit)
