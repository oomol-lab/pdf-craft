# pylint: disable=protected-access

from pathlib import Path
from typing import Literal, cast
from epub_generator import BookMeta, LaTeXRender, TableRender

from ...document import PDFCraftExtraction, RenderMode
from ...runtime import IO_DOMAIN, temporary_directory, run_atomic_cancellable
from ..output import publish


class EpubRenderer:
    """Render a PDFCraftExtraction to EPUB."""

    async def render(self, extraction: PDFCraftExtraction, output_path: Path, *,
                     book_meta: BookMeta | None = None,
                     lan: Literal["zh", "en"] | None = None,
                     table_render=TableRender.HTML,
                     latex_render=LaTeXRender.MATHML, inline_latex: bool = True,
                     mode: RenderMode = RenderMode.SOURCE,
                     translation_id: str | None = None,
                     aborted=lambda: False, on_body=None, on_footnote=None,
                     translator=None, window=1, with_anchored=True) -> None:
        """Orchestrate the view and native EPUB generation asynchronously."""
        from ...document.stream_view import content_view
        from ..paragraph import paragraph_notifications
        from .content import render_content
        async with content_view(extraction, mode=mode, translation_id=translation_id,
                                translator=translator, window=window,
                                with_anchored=with_anchored, aborted=aborted) as (view, pages, numbers, items):
            language = lan or view.language or "zh"
            effective_meta = _merge_book_meta(view.book_meta(), book_meta)
            if language not in {"zh", "en"}:
                raise ValueError(f"unsupported EPUB language: {language}")
            async with temporary_directory("pdf-craft-epub-") as directory:
                staged = Path(directory) / output_path.name
                async with paragraph_notifications(pages, view.assets, numbers, on_body, on_footnote) as notify:
                    await render_content(items, view, numbers, notify, Path(directory), staged,
                        mode=mode, book_meta=effective_meta, language=cast(Literal["zh", "en"], language),
                        table_render=table_render, latex_render=latex_render, inline_latex=inline_latex,
                        aborted=aborted)
                from ...metering import check_aborted
                check_aborted(aborted)
                await run_atomic_cancellable(IO_DOMAIN, lambda bridge: publish([(staged, output_path)], bridge.commit))


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
