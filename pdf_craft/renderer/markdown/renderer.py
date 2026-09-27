# pylint: disable=protected-access

from pathlib import Path
from ...document import PDFCraftExtraction, RenderMode
from ...document.render import materialize_render_view
from ...markdown.render import render_markdown_file
from ...runtime import IO_DOMAIN, run_cancellable

class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown."""

    @staticmethod
    def _render_blocking(
        extraction: PDFCraftExtraction, output_path: Path,
        assets_path: Path | None = None, cover_path: Path | None = None,
        aborted=lambda: False,
        *, mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
    ) -> None:
        with materialize_render_view(
            extraction, mode, translation_id,
        ) as view:
            render_markdown_file(view.chapters, view.assets, output_path,
                                 assets_path or Path("assets"),
                                 cover_path or (view.cover if view.cover.exists() else None), aborted)

    async def render(self, extraction: PDFCraftExtraction, output_path: Path,
                     assets_path: Path | None = None, cover_path: Path | None = None,
                     aborted=lambda: False, *, mode: RenderMode = RenderMode.SOURCE,
                     translation_id: str | None = None) -> None:
        """Render Markdown on the bounded filesystem execution domain."""
        await run_cancellable(
            IO_DOMAIN,
            lambda cancelled: self._render_blocking(
                extraction, output_path, assets_path, cover_path,
                lambda: cancelled() or aborted(), mode=mode,
                translation_id=translation_id,
            ),
            original_aborted=aborted,
        )
