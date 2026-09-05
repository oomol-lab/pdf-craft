# pylint: disable=protected-access

from pathlib import Path
from typing import Literal, cast
from ...document import PDFCraftExtraction
from .render import render_epub_file
from epub_generator import BookMeta, LaTeXRender, TableRender

class EpubRenderer:
    """Render a PDFCraftExtraction to EPUB."""
    def render(self, extraction: PDFCraftExtraction, output_path: Path, *,
               book_meta: BookMeta | None = None,
               lan: Literal["zh", "en"] | None = None, table_render=TableRender.HTML,
               latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
               aborted=lambda: False) -> None:
        extraction.validate(require_toc=True)
        language = lan or extraction.language() or "zh"
        book_meta = book_meta or extraction.book_meta()
        if language not in {"zh", "en"}:
            raise ValueError(f"unsupported EPUB language: {language}")
        language = cast(Literal["zh", "en"], language)
        with extraction._materialize() as paths:
            render_epub_file(paths.chapters, paths.toc, paths.assets,
                             output_path, paths.cover if paths.cover.exists() else None,
                             book_meta, language, table_render,
                             latex_render, inline_latex, aborted)
