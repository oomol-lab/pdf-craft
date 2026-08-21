from pathlib import Path
from typing import Literal
from ...document import DocumentPackage
from ...epub.render import render_epub_file
from epub_generator import LaTeXRender, TableRender

class EpubRenderer:
    """Render a stable DocumentPackage to EPUB."""
    def render(self, package: DocumentPackage, output_path: Path, *, book_meta=None,
               lan: Literal["zh", "en"] = "zh", table_render=TableRender.HTML,
               latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
               aborted=lambda: False) -> None:
        package.validate(require_toc=True)
        if lan not in {"zh", "en"}:
            raise ValueError(f"unsupported EPUB language: {lan}")
        render_epub_file(package.chapters_path, package.toc_path, package.assets_path,
                         output_path, package.cover_path, book_meta, lan, table_render,
                         latex_render, inline_latex, aborted)
