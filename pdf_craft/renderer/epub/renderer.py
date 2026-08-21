from pathlib import Path
from typing import Literal, cast
from ...document import DocumentPackage
from ...epub.render import render_epub_file
from epub_generator import LaTeXRender, TableRender

class EpubRenderer:
    """Render a stable DocumentPackage to EPUB."""
    def render(self, package: DocumentPackage, output_path: Path, *, book_meta=None,
               lan: str = "zh", table_render=TableRender.HTML,
               latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
               aborted=lambda: False) -> None:
        package.validate(require_toc=True)
        render_epub_file(package.chapters_path, package.toc_path, package.assets_path,
                         output_path, package.cover_path, book_meta, cast(Literal["zh", "en"], lan), table_render,
                         latex_render, inline_latex, aborted)
