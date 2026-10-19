"""Public facade that composes pdf-craft's independent components."""

# Internal workspace methods are intentionally shared only inside pdf-craft.
# pylint: disable=protected-access

import asyncio
import secrets
from collections.abc import Callable, Container
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path
from typing import AsyncIterator, Literal, cast

from epub_generator import BookMeta, LaTeXRender, TableRender

from .document import PDFCraftExtraction, RenderMode, TranslationInfo, resolve_translation
from .document.package import validate_translation_id
from .extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from .extractor.chapter.reader import create_chapters_reader
from .error import IgnoreFillErrorsChecker, IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .extractor import PDFExtractor
from .footnote import FootnoteOptions
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf.handler import _materialize_pdf_handler
from .pdf import (
    AsyncPDFHandler,
    DeepSeekOCRSize,
    OCRImageURLResolver,
    OCREvent,
    PDFHandler,
)
from .pipeline.pdf.error_policy import _process_ignore_errors_checker
from .pipeline.epub import translate_epub as run_epub_translation
from .pipeline.pdf import PDFTranslationPipeline
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import (
    AnchoredContentExtractionTransformer,
    AnchoredContentTransformer,
    SubmitKind,
    TranslationEvent,
    XMLTranslator,
)
from .transformer.narrative_xml import NarrativeXMLTransformer
from .transformer.package import (
    ChapterExtractionTransformer,
    FurnitureExtractionTransformer,
    _apply_metadata_overlay_to_workspace,
    append_translation_layer_to_workspace,
)
from .runtime import (
    IO_DOMAIN,
    temporary_directory, worker_context,
)
from .concurrency import AsyncExecutor


@dataclass(frozen=True)
class PDFOptions:
    """Long-lived infrastructure needed only when extracting a PDF."""

    ocr: OCRConfig | None = None
    pdf_handler: AsyncPDFHandler | None = None
    models_cache_path: PathLike | str | None = None
    local_only: bool = False
    ocr_executor: AsyncExecutor | None = None
    ocr_image_url_resolver: OCRImageURLResolver | None = None


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
    includes_furniture: bool = True
    extract_book_metadata: bool = False
    metadata_llm: LLM | None = None
    generate_plot: bool = False
    toc_assumed: bool = False
    toc_llm: LLM | None = None
    footnotes: FootnoteOptions | None = None
    ignore_pdf_errors: IgnorePDFErrorsChecker = False
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False
    aborted: AbortedCheck = lambda: False
    on_ocr_event: Callable[[OCREvent], object] = lambda _: None


class AsyncPDFCraft:
    """Async-first facade for extraction, rendering, and translation.

    Native async orchestration owns callbacks and cancellation. Synchronous
    native third-party operations run in dedicated domains while the event loop
    owns business orchestration and executable extensions.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._pdf = pdf
        self._engine = _engine

    @classmethod
    def from_engine(cls, engine) -> "AsyncPDFCraft":
        return cls(_engine=engine)

    async def predownload_models(
        self, *, models_cache_path: PathLike | None = None,
        revision: str | None = None, ocr: OCRConfig | None = None,
    ) -> None:
        """Prepare local OCR models through the async download implementation."""
        from .functions import predownload_models
        await predownload_models(models_cache_path=models_cache_path, revision=revision, ocr=ocr)

    async def open_extraction(self, path: PathLike | str) -> PDFCraftExtraction:
        """Open and validate a PCEX artifact without blocking the event loop."""
        return await IO_DOMAIN.run(PDFCraftExtraction._open, Path(path))

    async def export_extraction(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        path: PathLike | str,
    ) -> PDFCraftExtraction:
        """Export a validated PCEX handle through the owning facade."""
        document = await _ensure_extraction_async(extraction)
        return await document._export_async(Path(path))

    async def list_translations(
        self, extraction: PDFCraftExtraction | PathLike | str,
    ) -> tuple[TranslationInfo, ...]:
        """List the independently selectable translations stored in a PCEX."""
        document = await _ensure_extraction_async(extraction)
        return await IO_DOMAIN.run(document._translations)

    async def resolve_translation(
        self, extraction: PDFCraftExtraction | PathLike | str,
        translation_id: str | None = None,
    ) -> TranslationInfo:
        """Resolve an explicit translation or the stable first-index default."""
        document = await _ensure_extraction_async(extraction)
        return await IO_DOMAIN.run(resolve_translation, document, translation_id)

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
        footnotes = options.footnotes
        source_path = Path(source)
        async with self._pdf_extractor(source_path, options) as extractor:
            return await extractor.extract_with_metering(
                source_path, Path(extraction_path),
                analysing_path=(
                    Path(analysing_path) if analysing_path is not None else None
                ),
                page_indexes=options.page_indexes,
                ocr_size=options.ocr_size, dpi=options.dpi,
                max_page_image_file_size=options.max_page_image_file_size,
                max_tokens=options.max_ocr_tokens,
                max_output_tokens=options.max_ocr_output_tokens,
                includes_cover=options.includes_cover,
                includes_footnotes=footnotes is not None,
                includes_furniture=options.includes_furniture,
                extract_book_metadata=options.extract_book_metadata,
                metadata_llm=options.metadata_llm,
                generate_plot=options.generate_plot,
                toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
                footnote_refinement=(
                    footnotes.refinement if footnotes is not None else None
                ),
                ignore_pdf_errors=options.ignore_pdf_errors,
                ignore_ocr_errors=options.ignore_ocr_errors,
                aborted=options.aborted,
                on_ocr_event=options.on_ocr_event,
            )

    async def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await MarkdownRenderer().render(
            document, Path(output),
            Path(assets_path) if assets_path is not None else None,
            mode=mode, translation_id=translation_id,
            aborted=aborted,
        )

    async def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_anchored: bool = True,
        with_furniture: bool = False,
        translation_id: str | None = None,
        target_language: str | None = None,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        language = (
            target_language
            if target_language is not None
            else getattr(translator, "target_language", None)
        )
        if not isinstance(language, str) or not language.strip():
            raise ValueError(
                "target_language must be supplied or declared by the translator"
            )
        existing = {item.id for item in await IO_DOMAIN.run(document._translations)}
        selected_id = translation_id or _new_translation_id(existing)
        validate_translation_id(selected_id)
        if selected_id in existing:
            raise ValueError(f"translation_id already exists: {selected_id}")
        target = Path(output_path)
        if target.suffix.lower() != ".pcex":
            raise ValueError("PDFCraftExtraction path must end with .pcex")
        async with temporary_directory("pdf-craft-translated-extraction-") as root:
            layered = await self._translate_layer_to_workspace(
                document, root, translator, window=window,
                with_anchored=with_anchored, with_furniture=with_furniture,
                translation_id=selected_id, target_language=language,
                on_translation_event=on_translation_event,
            )
            return await layered._export_async(target)

    async def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        return await AnchoredContentExtractionTransformer(
            transformer,
        ).transform(
            document, Path(output_path),
        )

    async def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await EpubRenderer().render(
            document, Path(output), book_meta=book_meta, lan=lan,
            table_render=table_render, latex_render=latex_render,
            inline_latex=inline_latex, mode=mode,
            translation_id=translation_id, aborted=aborted,
        )

    async def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        async with temporary_directory("pdf-craft-translated-extraction-") as directory:
            translated = await self._translate_to_workspace(
                document, Path(directory), translator,
                window=window,
                with_anchored=False,
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
        *, translation_id: str | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        source_path = Path(source)
        output_path = Path(output)
        configured_handler = self._pdf.pdf_handler if self._pdf else None
        async with _process_ignore_errors_checker(ignore_errors) as worker_checker:
            if configured_handler is None:
                await _patch_pdf(
                    source_path, document, output_path, None, worker_checker,
                    translation_id,
                )
                return

            # Caller-owned handlers and callbacks can contain loops, locks, or
            # other non-pickleable state. Keep them in the caller process and
            # pass only materialized pages / an RPC checker into the Qt worker.
            page_indexes, dpi = await asyncio.gather(
                IO_DOMAIN.run(document._page_pixel_sizes),
                IO_DOMAIN.run(document._render_dpi),
            )
            page_indexes = tuple(sorted(page_indexes.keys()))
            async with temporary_directory("pdf-craft-pdf-pages-") as directory:
                materialized_handler = await _materialize_pdf_handler(
                    configured_handler, source_path, page_indexes, dpi, directory,
                )
                await _patch_pdf(
                    source_path, document, output_path,
                    materialized_handler, worker_checker, translation_id,
                )

    async def translate_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        target_language: str, submit: SubmitKind, **options,
    ) -> None:
        await run_epub_translation(
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
        translator: XMLTranslator | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        window: int = 1,
        with_anchored: bool = True,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> OCRTokensMetering:
        options = replace(extraction or ExtractionOptions(), includes_furniture=False)
        async with _analysis_workspace_async(analysing_path) as workspace:
            document, metering = await self._extract_to_workspace(source, workspace, options)
            if extraction_path is not None:
                await document._export_async(Path(extraction_path))
            if translator is not None:
                async with temporary_directory(
                    "pdf-craft-translated-extraction-"
                ) as directory:
                    document = await self._translate_layer_to_workspace(
                        document, Path(directory), translator,
                        window=window,
                        with_anchored=with_anchored,
                        on_translation_event=on_translation_event,
                    )
                    await self.render_markdown(
                        document, output, assets_path, aborted=options.aborted,
                        mode=_submission_render_mode(submit),
                        translation_id=(await IO_DOMAIN.run(document._translations))[-1].id,
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
        translator: XMLTranslator | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        window: int = 1,
        with_anchored: bool = True,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> OCRTokensMetering:
        options = replace(extraction or ExtractionOptions(), includes_furniture=False)
        async with _analysis_workspace_async(analysing_path) as workspace:
            document, metering = await self._extract_to_workspace(source, workspace, options)
            if extraction_path is not None:
                await document._export_async(Path(extraction_path))
            if translator is not None:
                async with temporary_directory(
                    "pdf-craft-translated-extraction-"
                ) as directory:
                    document = await self._translate_layer_to_workspace(
                        document, Path(directory), translator,
                        window=window,
                        with_anchored=with_anchored,
                        on_translation_event=on_translation_event,
                    )
                    await self.render_epub(
                        document, output, book_meta=book_meta,
                        # Preserve one-shot language inference: translator target
                        # names (e.g. "English") need not be EPUB language codes.
                        lan=lan or cast(Literal["zh", "en"],
                                        await IO_DOMAIN.run(document._language) or "zh"),
                        table_render=table_render, latex_render=latex_render,
                        inline_latex=inline_latex, aborted=options.aborted,
                        mode=_submission_render_mode(submit),
                        translation_id=(await IO_DOMAIN.run(document._translations))[-1].id,
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
        footnotes = options.footnotes
        source_path = Path(source)
        async with self._pdf_extractor(source_path, options) as extractor:
            return await extractor._extract_to_workspace_async(
                source_path, analysing_path,
                page_indexes=options.page_indexes,
                ocr_size=options.ocr_size, dpi=options.dpi,
                max_page_image_file_size=options.max_page_image_file_size,
                max_tokens=options.max_ocr_tokens,
                max_output_tokens=options.max_ocr_output_tokens,
                includes_cover=options.includes_cover,
                includes_footnotes=footnotes is not None,
                includes_furniture=options.includes_furniture,
                extract_book_metadata=options.extract_book_metadata,
                metadata_llm=options.metadata_llm,
                generate_plot=options.generate_plot,
                toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
                footnote_refinement=(
                    footnotes.refinement if footnotes is not None else None
                ),
                ignore_pdf_errors=options.ignore_pdf_errors,
                ignore_ocr_errors=options.ignore_ocr_errors,
                aborted=options.aborted, on_ocr_event=options.on_ocr_event,
            )

    async def _translate_layer_to_workspace(
        self, extraction: PDFCraftExtraction, workspace: Path,
        translator: XMLTranslator, *, window: int = 1,
        with_anchored: bool = True, with_furniture: bool = False,
        translation_id: str | None = None, target_language: str | None = None,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        """Shared persisted/one-shot translation: source plus one replacement layer."""
        existing = {item.id for item in await IO_DOMAIN.run(extraction._translations)}
        selected_id = translation_id or _new_translation_id(existing)
        language = target_language or getattr(translator, "target_language", None)
        if not isinstance(language, str) or not language.strip():
            # Custom one-shot translators historically need not advertise a language.
            language = await IO_DOMAIN.run(extraction._language) or "und"
        translated, overlay = await self._translate_replacement_to_workspace(
            extraction, workspace, translator, window=window,
            with_anchored=with_anchored, with_furniture=with_furniture,
            on_translation_event=on_translation_event,
        )
        return await IO_DOMAIN.run(
            append_translation_layer_to_workspace,
            extraction, translated, workspace / "layered",
            translation_id=selected_id, target_language=language,
            include_furniture=with_furniture, metadata_overlay=overlay,
        )

    async def _translate_to_workspace(
        self, extraction: PDFCraftExtraction, workspace: Path,
        translator: XMLTranslator, *, window: int = 1,
        with_anchored: bool = True, with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        """Materialize replacement-only root content for PDF writeback."""
        translated, overlay = await self._translate_replacement_to_workspace(
            extraction, workspace, translator, window=window,
            with_anchored=with_anchored, with_furniture=with_furniture,
            on_translation_event=on_translation_event,
        )
        if overlay:
            async with worker_context(translated._materialize()) as paths:
                translated = await IO_DOMAIN.run(
                    _apply_metadata_overlay_to_workspace, paths.root, overlay,
                )
        return translated

    async def _translate_replacement_to_workspace(
        self, extraction: PDFCraftExtraction, workspace: Path,
        translator: XMLTranslator, *,
        window: int = 1,
        with_anchored: bool = True,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> tuple[PDFCraftExtraction, dict]:
        narrative_transformer = NarrativeXMLTransformer(
            translator, mode=SubmitKind.REPLACE, window=window,
        )
        metadata_overlay = await narrative_transformer.translate_metadata(
            await IO_DOMAIN.run(extraction._document_metadata)
        )
        translated = await ChapterExtractionTransformer(
            narrative_transformer, mode=SubmitKind.REPLACE,
        )._transform_to_workspace_async(
            extraction,
            workspace / "narrative",
            on_translation_event=on_translation_event,
            emit_translation_events=True,
        )
        if with_anchored:
            translated = await AnchoredContentExtractionTransformer(
                narrative_transformer._anchored_transformer(),
            )._transform_to_workspace_async(translated, workspace / "anchored")
        if with_furniture:
            translated = await FurnitureExtractionTransformer(
                narrative_transformer._furniture_transformer(),
            )._transform_to_workspace_async(translated, workspace / "furniture")
        return translated, metadata_overlay

    @asynccontextmanager
    async def _pdf_extractor(
        self, source: Path, options: ExtractionOptions,
    ) -> AsyncIterator[PDFExtractor]:
        handler = self._pdf.pdf_handler if self._pdf is not None else None
        if handler is None or self._engine is not None:
            yield PDFExtractor(self._pdf_engine())
            return
        async with temporary_directory("pdf-craft-pdf-pages-") as directory:
            materialized = await _materialize_pdf_handler(
                handler,
                source,
                options.page_indexes,
                options.dpi if options.dpi is not None else 300,
                directory,
            )
            yield PDFExtractor(self._pdf_engine(materialized))

    def _pdf_engine(self, pdf_handler: PDFHandler | None = None):
        if self._engine is not None:
            return self._engine
        if self._pdf is None:
            raise ValueError("PDF extraction requires PDFCraft(pdf=PDFOptions(...))")
        from .transform import PDFExtractionEngine
        return PDFExtractionEngine(
            models_cache_path=self._pdf.models_cache_path,
            pdf_handler=pdf_handler,
            local_only=self._pdf.local_only,
            ocr=self._pdf.ocr,
            ocr_executor=self._pdf.ocr_executor,
            ocr_image_url_resolver=self._pdf.ocr_image_url_resolver,
        )


@asynccontextmanager
async def _analysis_workspace_async(
    analysing_path: PathLike | str | None,
) -> AsyncIterator[Path]:
    """Provide a persistent path or an asynchronously cleaned workspace."""
    if analysing_path is not None:
        yield Path(analysing_path)
        return
    async with temporary_directory("pdf-craft-analysis-") as directory:
        yield directory


async def _patch_pdf(
    source: Path,
    extraction: PDFCraftExtraction,
    output: Path,
    pdf_handler: PDFHandler | None,
    ignore_errors: IgnoreFillErrorsChecker,
    translation_id: str | None = None,
) -> None:
    await IO_DOMAIN.run(extraction._validate)
    if not _ignore_errors_requested(ignore_errors):
        await IO_DOMAIN.run(_validate_extraction_for_pdf, source, extraction)
    await PDFTranslationPipeline(_native_pdf_handler=pdf_handler).patch(
        source, output, extraction, translation_id=translation_id,
        ignore_errors=ignore_errors,
    )



def _validate_extraction_for_pdf(source: Path, extraction: PDFCraftExtraction) -> None:
    """Fail before patching when extraction geometry cannot match the PDF."""
    try:
        import pypdf
    except ImportError as error:
        raise RuntimeError("PDF patching requires the optional 'pypdf' dependency") from error
    page_sizes = extraction._page_pixel_sizes()
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


def _new_translation_id(existing: set[str]) -> str:
    while True:
        value = secrets.token_hex(4)
        if value not in existing:
            return value


async def _ensure_extraction_async(
    value: PDFCraftExtraction | PathLike | str,
) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return await IO_DOMAIN.run(PDFCraftExtraction._open, Path(value))


def _submission_render_mode(submit: SubmitKind) -> RenderMode:
    return {
        SubmitKind.REPLACE: RenderMode.REPLACE,
        SubmitKind.APPEND_TEXT: RenderMode.BILINGUAL_TEXT,
        SubmitKind.APPEND_BLOCK: RenderMode.BILINGUAL,
    }[submit]
