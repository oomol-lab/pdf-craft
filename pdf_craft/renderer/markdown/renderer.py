from pathlib import Path
from ...document import DocumentPackage
from ...markdown.render import render_markdown_file

class MarkdownRenderer:
    """Render a stable DocumentPackage to Markdown."""
    def render(self, package: DocumentPackage, output_path: Path,
               assets_path: Path | None = None, cover_path: Path | None = None,
               aborted=lambda: False) -> None:
        package.validate()
        render_markdown_file(package.chapters_path, package.assets_path, output_path,
                             assets_path or output_path.parent / "assets",
                             cover_path or package.cover_path, aborted)
