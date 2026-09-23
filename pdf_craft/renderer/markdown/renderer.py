# pylint: disable=protected-access

from pathlib import Path
from ...document import PDFCraftExtraction
from ...markdown.render import render_markdown_file
from ...runtime import IO_DOMAIN, run_cancellable

class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown."""

    @staticmethod
    def _render_blocking(
        extraction: PDFCraftExtraction, output_path: Path,
        assets_path: Path | None = None, cover_path: Path | None = None,
        aborted=lambda: False,
    ) -> None:
        extraction._validate()
        with extraction._materialize() as paths:
            render_markdown_file(paths.chapters, paths.assets, output_path,
                                 assets_path or Path("assets"),
                                 cover_path or (paths.cover if paths.cover.exists() else None), aborted)

    async def render(self, extraction: PDFCraftExtraction, output_path: Path,
                     assets_path: Path | None = None, cover_path: Path | None = None,
                     aborted=lambda: False) -> None:
        """Render Markdown on the bounded filesystem execution domain."""
        await run_cancellable(
            IO_DOMAIN,
            lambda cancelled: self._render_blocking(
                extraction, output_path, assets_path, cover_path,
                lambda: cancelled() or aborted(),
            ),
            original_aborted=aborted,
        )
