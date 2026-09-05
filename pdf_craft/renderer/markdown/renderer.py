# pylint: disable=protected-access

from pathlib import Path
from ...document import PDFCraftExtraction
from ...markdown.render import render_markdown_file

class MarkdownRenderer:
    """Render a PDFCraftExtraction to Markdown."""
    def render(self, extraction: PDFCraftExtraction, output_path: Path,
               assets_path: Path | None = None, cover_path: Path | None = None,
               aborted=lambda: False) -> None:
        extraction.validate()
        with extraction._materialize() as paths:
            render_markdown_file(paths.chapters, paths.assets, output_path,
                                 assets_path or Path("assets"),
                                 cover_path or (paths.cover if paths.cover.exists() else None), aborted)
