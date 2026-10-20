# pylint: disable=protected-access

from pathlib import Path
from typing import Literal, cast
from epub_generator import BookMeta, LaTeXRender, TableRender

from ...document import PDFCraftExtraction, RenderMode
from ...document.render import materialize_render_view
from ...document.content import consume_content, read_content
from .render import render_epub_file
from ...runtime import IO_DOMAIN, worker_context, temporary_directory, run_atomic_cancellable
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
                     aborted=lambda: False, on_body=None, on_footnote=None) -> None:
        """Orchestrate the view and native EPUB generation asynchronously."""
        async with worker_context(materialize_render_view(
            extraction, mode, translation_id, require_toc=True,
        )) as view:
            await consume_content(read_content(view.chapters), on_body=on_body, on_footnote=on_footnote, aborted=aborted)
            language = lan or view.language or "zh"
            effective_meta = _merge_book_meta(view.book_meta(), book_meta)
            if language not in {"zh", "en"}:
                raise ValueError(f"unsupported EPUB language: {language}")
            cover = view.cover if await IO_DOMAIN.run(view.cover.exists) else None
            async with temporary_directory("pdf-craft-epub-") as directory:
                staged = Path(directory) / output_path.name
                await render_epub_file(
                    view.chapters, view.toc, view.assets, staged, cover,
                    effective_meta, cast(Literal["zh", "en"], language),
                    table_render, latex_render, inline_latex, aborted,
                )
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
