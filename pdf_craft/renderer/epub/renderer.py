# pylint: disable=protected-access

from pathlib import Path
from typing import Literal, cast
from epub_generator import BookMeta, LaTeXRender, TableRender

from ...document import PDFCraftExtraction
from .render import render_epub_file
from ...runtime import TEX_DOMAIN, run_cancellable


class EpubRenderer:
    """Render a PDFCraftExtraction to EPUB."""

    @staticmethod
    def _render_blocking(
        extraction: PDFCraftExtraction, output_path: Path, *,
        book_meta: BookMeta | None = None,
        lan: Literal["zh", "en"] | None = None, table_render=TableRender.HTML,
        latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
        aborted=lambda: False,
    ) -> None:
        extraction._validate(require_toc=True)
        language = lan or extraction._language() or "zh"
        book_meta = _merge_book_meta(extraction._book_meta(), book_meta)
        if language not in {"zh", "en"}:
            raise ValueError(f"unsupported EPUB language: {language}")
        language = cast(Literal["zh", "en"], language)
        with extraction._materialize() as paths:
            render_epub_file(paths.chapters, paths.toc, paths.assets,
                             output_path, paths.cover if paths.cover.exists() else None,
                             book_meta, language, table_render,
                             latex_render, inline_latex, aborted)

    async def render(self, extraction: PDFCraftExtraction, output_path: Path, *,
                     book_meta: BookMeta | None = None,
                     lan: Literal["zh", "en"] | None = None,
                     table_render=TableRender.HTML,
                     latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
                     aborted=lambda: False) -> None:
        """Keep epub-generator and ZIP I/O off the event-loop thread."""
        await run_cancellable(
            TEX_DOMAIN,
            lambda cancelled: self._render_blocking(
                extraction, output_path, book_meta=book_meta, lan=lan,
                table_render=table_render, latex_render=latex_render,
                inline_latex=inline_latex,
                aborted=lambda: cancelled() or aborted(),
            ),
            original_aborted=aborted,
        )


def _merge_book_meta(extracted: BookMeta | None, explicit: BookMeta | None) -> BookMeta | None:
    """Overlay non-empty caller fields without discarding extracted PCEX metadata.

    ``BookMeta`` has no presence markers, so ``None``/empty strings and empty
    contributor lists mean "leave the extracted value alone" rather than
    "clear it". Callers that need an intentionally blank field must render a
    PCEX whose manifest already contains that value.
    """
    if explicit is None:
        return extracted
    base = extracted or BookMeta()
    return BookMeta(
        title=_text_override(explicit.title, base.title),
        description=_text_override(explicit.description, base.description),
        publisher=_text_override(explicit.publisher, base.publisher),
        isbn=_text_override(explicit.isbn, base.isbn),
        authors=list(explicit.authors) if explicit.authors else list(base.authors),
        editors=list(explicit.editors) if explicit.editors else list(base.editors),
        translators=list(explicit.translators) if explicit.translators else list(base.translators),
        modified=explicit.modified or base.modified,
    )


def _text_override(explicit: str | None, extracted: str | None) -> str | None:
    return explicit if explicit is not None and explicit.strip() else extracted
