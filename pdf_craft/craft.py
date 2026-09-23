"""Public facade that composes pdf-craft's independent components."""

# Internal workspace methods are intentionally shared only inside pdf-craft.
# pylint: disable=protected-access

import asyncio
from collections.abc import Callable, Container
from contextlib import contextmanager
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Literal

from epub_generator import BookMeta, LaTeXRender, TableRender

from .document import PDFCraftExtraction
from .extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from .extractor.chapter.reader import create_chapters_reader
from .error import IgnoreFillErrorsChecker, IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .extractor import PDFExtractor
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf import DeepSeekOCRSize, OCREvent, PDFHandler
from .pipeline.epub import (
    translate_epub as run_epub_translation,
    translate_epub_async as run_epub_translation_async,
)
from .pipeline.pdf import PDFTranslationPipeline
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import (
    ChapterExtractionTransformer,
    ChapterTransformer,
    AnchoredContentExtractionTransformer,
    AnchoredContentTransformer,
    SubmitKind,
    TranslationEvent,
)
from .transformer.chapter_xml import ChapterXMLTransformer
from .transformer.furniture_xml import FurnitureXMLTransformer
from .transformer.package import FurnitureExtractionTransformer
from .runtime import (
    IO_DOMAIN,
    QT_DOMAIN,
    TRANSLATION_DOMAIN,
    callback_bridge,
    require_sync_context,
)


@dataclass(frozen=True)
class PDFOptions:
    """Long-lived infrastructure needed only when extracting a PDF."""

    ocr: OCRConfig | None = None
    pdf_handler: PDFHandler | None = None
    models_cache_path: PathLike | str | None = None
    local_only: bool = False


@dataclass(frozen=True)
class ExtractionOptions:
    """Controls for one PDF extraction run."""

    page_indexes: Container[int] | None = None
    ocr_size: DeepSeekOCRSize = "gundam"
    dpi: int | None = None
    max_page_image_file_size: int | None = None
    max_ocr_tokens: int | None = None
    max_ocr_output_tokens: int | None = None
    includes_cover: bool = False
    includes_footnotes: bool = False
    includes_furniture: bool = True
    extract_book_metadata: bool = False
    metadata_llm: LLM | None = None
    generate_plot: bool = False
    toc_assumed: bool = False
    toc_llm: LLM | None = None
    ignore_pdf_errors: IgnorePDFErrorsChecker = False
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False
    aborted: AbortedCheck = lambda: False
    on_ocr_event: Callable[[OCREvent], object] = lambda _: None


class PDFCraft:
    """Compose extraction, rendering, and format-specific translation workflows.

    Constructing this facade does not initialise OCR.  EPUB-only callers can
    therefore use ``PDFCraft()`` without PDF infrastructure or credentials.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._pdf = pdf
        self._engine = _engine

    @classmethod
    def from_engine(cls, engine) -> "PDFCraft":
        return cls(_engine=engine)

    def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> PDFCraftExtraction:
        require_sync_context()
        extraction, _ = self.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path
        )
        return extraction

    def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        require_sync_context()
        options = options or ExtractionOptions()
        return PDFExtractor(self._pdf_engine()).extract_with_metering(
            Path(source), Path(extraction_path),
            analysing_path=Path(analysing_path) if analysing_path is not None else None,
            page_indexes=options.page_indexes,
            ocr_size=options.ocr_size, dpi=options.dpi,
            max_page_image_file_size=options.max_page_image_file_size,
            max_tokens=options.max_ocr_tokens,
            max_output_tokens=options.max_ocr_output_tokens,
            includes_cover=options.includes_cover,
            includes_footnotes=options.includes_footnotes,
            includes_furniture=options.includes_furniture,
            extract_book_metadata=options.extract_book_metadata,
            metadata_llm=options.metadata_llm,
            generate_plot=options.generate_plot,
            toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
            ignore_pdf_errors=options.ignore_pdf_errors,
            ignore_ocr_errors=options.ignore_ocr_errors,
            aborted=options.aborted, on_ocr_event=options.on_ocr_event,
        )

    def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        require_sync_context()
        MarkdownRenderer().render(_ensure_extraction(extraction), Path(output),
                                  Path(assets_path) if assets_path is not None else None,
                                  aborted=aborted)

    def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: ChapterTransformer,
        *, submit: SubmitKind = SubmitKind.REPLACE,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        """Translate one PDFCraftExtraction into another ``.pcex`` artifact.

        ``with_furniture`` composes the page-furniture pass into the same public
        operation.  It is intentionally separate from extraction's
        ``includes_furniture``: an existing pcex may or may not contain that
        optional source layer.
        """
        require_sync_context()
        source = _ensure_extraction(extraction)
        target = Path(output_path)
        extraction_transformer = ChapterExtractionTransformer(translator, mode=submit)
        if not with_furniture:
            return extraction_transformer.transform(
                source, target, on_translation_event=on_translation_event,
                emit_translation_events=True,
            )
        if target.suffix.lower() != ".pcex":
            raise ValueError("PDFCraftExtraction path must end with .pcex")
        furniture_transformer = _furniture_transformer_for(extraction_transformer.chapter_transformer)
        with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
            root = Path(directory)
            narrative = extraction_transformer._transform_to_workspace(
                source, root / "narrative", on_translation_event=on_translation_event,
                emit_translation_events=True,
            )
            translated = FurnitureExtractionTransformer(furniture_transformer)._transform_to_workspace(
                narrative, root / "translated",
            )
            return translated.export(target)

    def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer,
    ) -> PDFCraftExtraction:
        """Translate extracted image/table text without entering NarrativeFlow."""
        require_sync_context()
        return AnchoredContentExtractionTransformer(transformer).transform(
            _ensure_extraction(extraction), Path(output_path)
        )

    def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        require_sync_context()
        EpubRenderer().render(_ensure_extraction(extraction), Path(output), book_meta=book_meta, lan=lan,
                              table_render=table_render, latex_render=latex_render,
                              inline_latex=inline_latex, aborted=aborted)

    def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str, transformer: ChapterTransformer,
        *,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        require_sync_context()
        with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
            translated = self.translate_extraction(
                _ensure_extraction(extraction), Path(directory) / "translated.pcex", transformer,
                with_furniture=with_furniture,
                on_translation_event=on_translation_event,
            )
            self.patch_pdf_with_extraction(source, translated, output, ignore_errors=ignore_errors)

    def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        *,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        """Patch an existing PDF with text and geometry from a PDFCraftExtraction."""
        require_sync_context()
        extraction = _ensure_extraction(extraction)
        extraction.validate()
        if not _ignore_errors_requested(ignore_errors):
            _validate_extraction_for_pdf(Path(source), extraction)
        PDFTranslationPipeline(
            pdf_handler=self._pdf.pdf_handler if self._pdf else None
        ).patch(Path(source), Path(output), extraction, ignore_errors=ignore_errors)

    def translate_epub(self, source: PathLike | str, output: PathLike | str, *,
                       target_language: str, submit: SubmitKind,
                       **options) -> None:
        require_sync_context()
        run_epub_translation(source, output, target_language, submit, **options)

    def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        require_sync_context()
        extraction = replace(extraction or ExtractionOptions(), includes_furniture=False)
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = self._extract_to_workspace(source, workspace, extraction)
            if extraction_path is not None:
                document.export(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    self.render_markdown(document, output, assets_path,
                                         aborted=extraction.aborted)
            else:
                self.render_markdown(document, output, assets_path,
                                     aborted=extraction.aborted)
        return metering

    def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        require_sync_context()
        extraction = replace(extraction or ExtractionOptions(), includes_furniture=False)
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = self._extract_to_workspace(source, workspace, extraction)
            if extraction_path is not None:
                document.export(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    self.render_epub(document, output, book_meta=book_meta, lan=lan,
                                     table_render=table_render, latex_render=latex_render,
                                     inline_latex=inline_latex, aborted=extraction.aborted)
            else:
                self.render_epub(document, output, book_meta=book_meta, lan=lan,
                                 table_render=table_render, latex_render=latex_render,
                                 inline_latex=inline_latex, aborted=extraction.aborted)
        return metering

    def _translate_to_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        transformer: ChapterTransformer,
        *,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        return ChapterExtractionTransformer(transformer, mode=submit)._transform_to_workspace(
            extraction, output_path, on_translation_event=on_translation_event,
            emit_translation_events=True,
        )

    def _extract_to_workspace(
        self,
        source: PathLike | str,
        analysing_path: Path,
        options: ExtractionOptions | None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        options = options or ExtractionOptions()
        return PDFExtractor(self._pdf_engine())._extract_to_workspace(
            Path(source), analysing_path,
            page_indexes=options.page_indexes,
            ocr_size=options.ocr_size, dpi=options.dpi,
            max_page_image_file_size=options.max_page_image_file_size,
            max_tokens=options.max_ocr_tokens,
            max_output_tokens=options.max_ocr_output_tokens,
            includes_cover=options.includes_cover,
            includes_footnotes=options.includes_footnotes,
            includes_furniture=options.includes_furniture,
            extract_book_metadata=options.extract_book_metadata,
            metadata_llm=options.metadata_llm,
            generate_plot=options.generate_plot,
            toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
            ignore_pdf_errors=options.ignore_pdf_errors,
            ignore_ocr_errors=options.ignore_ocr_errors,
            aborted=options.aborted, on_ocr_event=options.on_ocr_event,
        )

    def _pdf_engine(self):
        if self._engine is not None:
            return self._engine
        if self._pdf is None:
            raise ValueError("PDF extraction requires PDFCraft(pdf=PDFOptions(...))")
        # Import lazily so EPUB-only callers never import the historical adapter.
        from .transform import PDFExtractionEngine
        return PDFExtractionEngine(models_cache_path=self._pdf.models_cache_path,
                                   pdf_handler=self._pdf.pdf_handler,
                                   local_only=self._pdf.local_only, ocr=self._pdf.ocr)


class AsyncPDFCraft:
    """Async-first facade for extraction, rendering, and translation.

    Native async orchestration owns callbacks and cancellation. Synchronous
    third-party pipelines are executed as coarse operations in their dedicated
    domains so their internal objects never escape across worker threads.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._sync = PDFCraft(pdf=pdf, _engine=_engine)

    @classmethod
    def from_engine(cls, engine) -> "AsyncPDFCraft":
        return cls(_engine=engine)

    async def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> PDFCraftExtraction:
        extraction, _ = await self.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path,
        )
        return extraction

    async def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        options = options or ExtractionOptions()
        loop = asyncio.get_running_loop()
        worker_options = replace(
            options,
            on_ocr_event=callback_bridge(loop, options.on_ocr_event),
        )
        return await PDFExtractor(self._sync._pdf_engine()).extract_with_metering_async(
            Path(source), Path(extraction_path),
            analysing_path=Path(analysing_path) if analysing_path is not None else None,
            page_indexes=worker_options.page_indexes,
            ocr_size=worker_options.ocr_size, dpi=worker_options.dpi,
            max_page_image_file_size=worker_options.max_page_image_file_size,
            max_tokens=worker_options.max_ocr_tokens,
            max_output_tokens=worker_options.max_ocr_output_tokens,
            includes_cover=worker_options.includes_cover,
            includes_footnotes=worker_options.includes_footnotes,
            includes_furniture=worker_options.includes_furniture,
            extract_book_metadata=worker_options.extract_book_metadata,
            metadata_llm=worker_options.metadata_llm,
            generate_plot=worker_options.generate_plot,
            toc_assumed=worker_options.toc_assumed, toc_llm=worker_options.toc_llm,
            ignore_pdf_errors=worker_options.ignore_pdf_errors,
            ignore_ocr_errors=worker_options.ignore_ocr_errors,
            aborted=worker_options.aborted,
            on_ocr_event=worker_options.on_ocr_event,
        )

    async def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await MarkdownRenderer().render_async(
            document, Path(output),
            Path(assets_path) if assets_path is not None else None,
            aborted=aborted,
        )

    async def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: ChapterTransformer,
        *, submit: SubmitKind = SubmitKind.REPLACE,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        extraction_transformer = ChapterExtractionTransformer(translator, mode=submit)
        if isinstance(extraction_transformer.chapter_transformer, ChapterXMLTransformer):
            if not with_furniture:
                return await extraction_transformer.transform_async(
                    document,
                    Path(output_path),
                    on_translation_event=on_translation_event,
                    emit_translation_events=True,
                )
            target = Path(output_path)
            if target.suffix.lower() != ".pcex":
                raise ValueError("PDFCraftExtraction path must end with .pcex")
            temporary = await IO_DOMAIN.run(
                TemporaryDirectory, prefix="pdf-craft-translated-extraction-",
            )
            try:
                root = Path(temporary.name)
                narrative = await extraction_transformer._transform_to_workspace_async(
                    document,
                    root / "narrative",
                    on_translation_event=on_translation_event,
                    emit_translation_events=True,
                )
                furniture_transformer = _furniture_transformer_for(
                    extraction_transformer.chapter_transformer,
                )
                translated = await FurnitureExtractionTransformer(
                    furniture_transformer,
                )._transform_to_workspace_async(narrative, root / "translated")
                return await translated.export_async(target)
            finally:
                await IO_DOMAIN.run(temporary.cleanup)
        callback = callback_bridge(asyncio.get_running_loop(), on_translation_event)
        return await TRANSLATION_DOMAIN.run(
            self._sync.translate_extraction,
            document, output_path, translator,
            submit=submit, with_furniture=with_furniture,
            on_translation_event=callback,
        )

    async def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        return await AnchoredContentExtractionTransformer(
            transformer,
        ).transform_async(
            document, Path(output_path),
        )

    async def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await EpubRenderer().render_async(
            document, Path(output), book_meta=book_meta, lan=lan,
            table_render=table_render, latex_render=latex_render,
            inline_latex=inline_latex, aborted=aborted,
        )

    async def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str, transformer: ChapterTransformer,
        *, with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
            translated = await self.translate_extraction(
                document, Path(directory) / "translated.pcex", transformer,
                with_furniture=with_furniture,
                on_translation_event=on_translation_event,
            )
            await self.patch_pdf_with_extraction(
                source, translated, output, ignore_errors=ignore_errors,
            )

    async def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        *, ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        # A single stable worker owns QGuiApplication, QTextLayout, pypdf and
        # reportlab objects for the complete patch operation.
        await QT_DOMAIN.run(
            self._sync.patch_pdf_with_extraction,
            source, document, output, ignore_errors=ignore_errors,
        )

    async def translate_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        target_language: str, submit: SubmitKind, **options,
    ) -> None:
        await run_epub_translation_async(
            source,
            output,
            target_language=target_language,
            submit=submit,
            **options,
        )

    async def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> OCRTokensMetering:
        options = replace(extraction or ExtractionOptions(), includes_furniture=False)
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = await self._extract_to_workspace(source, workspace, options)
            if extraction_path is not None:
                await document.export_async(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = await self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    await self.render_markdown(
                        document, output, assets_path, aborted=options.aborted,
                    )
            else:
                await self.render_markdown(document, output, assets_path, aborted=options.aborted)
        return metering

    async def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> OCRTokensMetering:
        options = replace(extraction or ExtractionOptions(), includes_furniture=False)
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = await self._extract_to_workspace(source, workspace, options)
            if extraction_path is not None:
                await document.export_async(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = await self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    await self.render_epub(
                        document, output, book_meta=book_meta, lan=lan,
                        table_render=table_render, latex_render=latex_render,
                        inline_latex=inline_latex, aborted=options.aborted,
                    )
            else:
                await self.render_epub(
                    document, output, book_meta=book_meta, lan=lan,
                    table_render=table_render, latex_render=latex_render,
                    inline_latex=inline_latex, aborted=options.aborted,
                )
        return metering

    async def _extract_to_workspace(
        self, source: PathLike | str, analysing_path: Path,
        options: ExtractionOptions,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        loop = asyncio.get_running_loop()
        callback = callback_bridge(loop, options.on_ocr_event)
        return await PDFExtractor(self._sync._pdf_engine())._extract_to_workspace_async(
            Path(source), analysing_path,
            page_indexes=options.page_indexes,
            ocr_size=options.ocr_size, dpi=options.dpi,
            max_page_image_file_size=options.max_page_image_file_size,
            max_tokens=options.max_ocr_tokens,
            max_output_tokens=options.max_ocr_output_tokens,
            includes_cover=options.includes_cover,
            includes_footnotes=options.includes_footnotes,
            includes_furniture=options.includes_furniture,
            extract_book_metadata=options.extract_book_metadata,
            metadata_llm=options.metadata_llm,
            generate_plot=options.generate_plot,
            toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
            ignore_pdf_errors=options.ignore_pdf_errors,
            ignore_ocr_errors=options.ignore_ocr_errors,
            aborted=options.aborted, on_ocr_event=callback,
        )

    async def _translate_to_workspace(
        self, extraction: PDFCraftExtraction, output_path: Path,
        transformer: ChapterTransformer, *,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        extraction_transformer = ChapterExtractionTransformer(transformer, mode=submit)
        if isinstance(extraction_transformer.chapter_transformer, ChapterXMLTransformer):
            return await extraction_transformer._transform_to_workspace_async(
                extraction,
                output_path,
                on_translation_event=on_translation_event,
                emit_translation_events=True,
            )
        callback = callback_bridge(asyncio.get_running_loop(), on_translation_event)
        return await TRANSLATION_DOMAIN.run(
            self._sync._translate_to_workspace,
            extraction, output_path, transformer,
            submit=submit, on_translation_event=callback,
        )


@contextmanager
def _analysis_workspace(analysing_path: PathLike | str | None) -> Iterator[Path]:
    """Provide a persistent analysis path or a cleaned-up temporary workspace."""
    if analysing_path is not None:
        yield Path(analysing_path)
        return
    with TemporaryDirectory(prefix="pdf-craft-analysis-") as directory:
        yield Path(directory)


def _validate_extraction_for_pdf(source: Path, extraction: PDFCraftExtraction) -> None:
    """Fail before patching when extraction geometry cannot match the PDF."""
    try:
        import pypdf
    except ImportError as error:
        raise RuntimeError("PDF patching requires the optional 'pypdf' dependency") from error
    page_sizes = extraction.page_pixel_sizes()
    if not page_sizes:
        raise ValueError("PDFCraftExtraction is missing page geometry required for PDF patching")
    page_count = len(pypdf.PdfReader(str(source)).pages)
    with extraction._materialize() as paths:
        chapter_pages = {
            fragment.page_index
            for chapter in create_chapters_reader(paths.chapters)()
            for item in chapter.flow_items
            if isinstance(item, TextFlowItem)
            for fragment in item.children
            if isinstance(fragment, SourceTextFragment)
        }
    invalid = sorted(page for page in set(page_sizes) | chapter_pages if page > page_count)
    if invalid:
        raise ValueError(
            "PDFCraftExtraction page geometry exceeds source PDF page count: "
            f"pages {invalid} of {page_count}"
        )
    missing = sorted(page for page in chapter_pages if page not in page_sizes)
    if missing:
        raise ValueError(
            "PDFCraftExtraction is missing page geometry for chapter pages: "
            f"{missing}"
        )


def _ignore_errors_requested(checker: IgnoreFillErrorsChecker) -> bool:
    """Defer page-addressable validation when a fill recovery policy exists."""
    return checker is True or callable(checker)


def _furniture_transformer_for(transformer: ChapterTransformer) -> FurnitureXMLTransformer:
    """Reuse the XML translation runtime for the optional furniture pass."""
    if not isinstance(transformer, ChapterXMLTransformer):
        raise ValueError("with_furniture=True requires a ChapterXMLTransformer")
    return transformer._furniture_transformer()


def _ensure_extraction(value: PDFCraftExtraction | PathLike | str) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return PDFCraftExtraction.open(Path(value))


async def _ensure_extraction_async(
    value: PDFCraftExtraction | PathLike | str,
) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return await PDFCraftExtraction.open_async(Path(value))
