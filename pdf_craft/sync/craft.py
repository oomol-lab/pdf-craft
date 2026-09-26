"""Synchronous compatibility wrapper around :class:`AsyncPDFCraft`."""

from collections.abc import Callable
from os import PathLike
from typing import Literal

from epub_generator import BookMeta, LaTeXRender, TableRender

from ..craft import AsyncPDFCraft, ExtractionOptions, PDFOptions
from ..document import PDFCraftExtraction, TranslationInfo
from ..error import IgnoreFillErrorsChecker
from ..metering import AbortedCheck, OCRTokensMetering
from ..runtime import run_sync
from ..transformer import (
    AnchoredContentTransformer,
    ChapterTransformer,
    SyncAnchoredContentTransformer,
    SyncChapterTransformer,
    SubmitKind,
    TranslationEvent,
)


class PDFCraft:
    """Synchronous compatibility facade over the async-first SDK."""

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._async_craft = AsyncPDFCraft(pdf=pdf, _engine=_engine)

    @classmethod
    def from_engine(cls, engine) -> "PDFCraft":
        return cls(_engine=engine)

    def open_extraction(self, path: PathLike | str) -> PDFCraftExtraction:
        return run_sync(self._async_craft.open_extraction(path))

    def export_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, path: PathLike | str,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.export_extraction(extraction, path))

    def list_translations(
        self, extraction: PDFCraftExtraction | PathLike | str,
    ) -> tuple[TranslationInfo, ...]:
        return run_sync(self._async_craft.list_translations(extraction))

    def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.extract_pdf(
            source, extraction_path, options, analysing_path=analysing_path,
        ))

    def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        return run_sync(self._async_craft.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path,
        ))

    def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        run_sync(self._async_craft.render_markdown(
            extraction, output, assets_path, aborted=aborted,
        ))

    def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: ChapterTransformer | SyncChapterTransformer,
        *, submit: SubmitKind = SubmitKind.REPLACE,
        with_furniture: bool = False,
        translation_id: str | None = None,
        target_language: str | None = None,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.translate_extraction(
            extraction, output_path, translator, submit=submit,
            with_furniture=with_furniture,
            translation_id=translation_id, target_language=target_language,
            on_translation_event=on_translation_event,
        ))

    def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer | SyncAnchoredContentTransformer,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.translate_anchored_contents(
            extraction, output_path, transformer,
        ))

    def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        run_sync(self._async_craft.render_epub(
            extraction, output, book_meta=book_meta, lan=lan,
            table_render=table_render, latex_render=latex_render,
            inline_latex=inline_latex, aborted=aborted,
        ))

    def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        transformer: ChapterTransformer | SyncChapterTransformer,
        *,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        run_sync(self._async_craft.translate_pdf(
            source, extraction, output, transformer,
            with_furniture=with_furniture,
            on_translation_event=on_translation_event,
            ignore_errors=ignore_errors,
        ))

    def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        *,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        run_sync(self._async_craft.patch_pdf_with_extraction(
            source, extraction, output, ignore_errors=ignore_errors,
        ))

    def translate_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        target_language: str, submit: SubmitKind, **options,
    ) -> None:
        run_sync(self._async_craft.translate_epub(
            source, output, target_language=target_language, submit=submit, **options,
        ))

    def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        translator: ChapterTransformer | SyncChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        return run_sync(self._async_craft.convert_pdf_to_markdown(
            source, output, analysing_path=analysing_path,
            extraction_path=extraction_path, extraction=extraction,
            assets_path=assets_path, translator=translator, submit=submit,
            on_translation_event=on_translation_event,
        ))

    def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: ChapterTransformer | SyncChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        return run_sync(self._async_craft.convert_pdf_to_epub(
            source, output, analysing_path=analysing_path,
            extraction_path=extraction_path, extraction=extraction,
            book_meta=book_meta, lan=lan, table_render=table_render,
            latex_render=latex_render, inline_latex=inline_latex,
            translator=translator, submit=submit,
            on_translation_event=on_translation_event,
        ))
