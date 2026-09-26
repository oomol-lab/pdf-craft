"""Public facade that composes pdf-craft's independent components."""

# Internal workspace methods are intentionally shared only inside pdf-craft.
# pylint: disable=protected-access

import asyncio
import inspect
import pickle
import secrets
from collections.abc import Awaitable, Callable, Container
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from multiprocessing.connection import Client, Listener
from os import PathLike
from pathlib import Path
from threading import Thread
from typing import Any, AsyncIterator, Literal, cast

from epub_generator import BookMeta, LaTeXRender, TableRender
from PIL import Image

from .document import PDFCraftExtraction, TranslationInfo
from .document.package import validate_translation_id
from .extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from .extractor.chapter.reader import create_chapters_reader
from .error import IgnoreFillErrorsChecker, IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .extractor import PDFExtractor
from .footnote import FootnoteOptions
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf import (
    AsyncPDFDocument,
    AsyncPDFHandler,
    DeepSeekOCRSize,
    OCREvent,
    PDFDocument,
    PDFDocumentMetadata,
    PDFHandler,
)
from .pipeline.epub import translate_epub as run_epub_translation
from .pipeline.pdf import PDFTranslationPipeline
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import (
    ChapterExtractionTransformer,
    ChapterTransformer,
    SyncAnchoredContentTransformer,
    SyncChapterTransformer,
    AnchoredContentExtractionTransformer,
    AnchoredContentTransformer,
    SubmitKind,
    TranslationEvent,
)
from .transformer.chapter_xml import ChapterXMLTransformer
from .transformer.furniture_xml import FurnitureXMLTransformer
from .transformer.package import (
    FurnitureExtractionTransformer, append_translation_layer_to_workspace,
)
from .runtime import (
    IO_DOMAIN,
    QT_DOMAIN,
    temporary_directory,
)


@dataclass(frozen=True)
class PDFOptions:
    """Long-lived infrastructure needed only when extracting a PDF."""

    ocr: OCRConfig | None = None
    pdf_handler: PDFHandler | AsyncPDFHandler | None = None
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
    third-party pipelines are executed as coarse operations in their dedicated
    domains so their internal objects never escape across worker threads.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._pdf = pdf
        self._engine = _engine

    @classmethod
    def from_engine(cls, engine) -> "AsyncPDFCraft":
        return cls(_engine=engine)

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
        return await PDFExtractor(self._pdf_engine()).extract_with_metering(
            Path(source), Path(extraction_path),
            analysing_path=Path(analysing_path) if analysing_path is not None else None,
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
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await MarkdownRenderer().render(
            document, Path(output),
            Path(assets_path) if assets_path is not None else None,
            aborted=aborted,
        )

    async def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: ChapterTransformer | SyncChapterTransformer,
        *, submit: SubmitKind = SubmitKind.REPLACE,
        with_furniture: bool = False,
        translation_id: str | None = None,
        target_language: str | None = None,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        if submit != SubmitKind.REPLACE:
            raise ValueError("PCEX translation layers store replacement text; choose a render mode later")
        language = target_language or _transformer_target_language(translator) or "und"
        if not language.strip():
            raise ValueError("target_language must be a non-empty string")
        existing = {item.id for item in await IO_DOMAIN.run(document._translations)}
        selected_id = translation_id or _new_translation_id(existing)
        validate_translation_id(selected_id)
        if selected_id in existing:
            raise ValueError(f"translation_id already exists: {selected_id}")
        extraction_transformer = ChapterExtractionTransformer(
            translator, mode=SubmitKind.REPLACE,
        )
        if with_furniture and not isinstance(
            extraction_transformer.chapter_transformer, ChapterXMLTransformer,
        ):
            raise ValueError("with_furniture=True requires a ChapterXMLTransformer")
        target = Path(output_path)
        if target.suffix.lower() != ".pcex":
            raise ValueError("PDFCraftExtraction path must end with .pcex")
        async with temporary_directory(
            "pdf-craft-translated-extraction-"
        ) as root:
            metadata_overlay = (
                await extraction_transformer.chapter_transformer.translate_metadata(
                    await IO_DOMAIN.run(document._document_metadata)
                )
                if isinstance(
                    extraction_transformer.chapter_transformer, ChapterXMLTransformer,
                )
                else {}
            )
            translated = await extraction_transformer._transform_to_workspace_async(
                document,
                root / "narrative",
                on_translation_event=on_translation_event,
                emit_translation_events=True,
            )
            if with_furniture:
                furniture_transformer = _furniture_transformer_for(
                    extraction_transformer.chapter_transformer,
                )
                translated = await FurnitureExtractionTransformer(
                    furniture_transformer,
                )._transform_to_workspace_async(translated, root / "translated")
            layered = await IO_DOMAIN.run(
                append_translation_layer_to_workspace,
                document,
                translated,
                root / "layered",
                translation_id=selected_id,
                target_language=language,
                include_furniture=with_furniture,
                metadata_overlay=metadata_overlay,
            )
            return await layered._export_async(target)

    async def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer | SyncAnchoredContentTransformer,
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
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await EpubRenderer().render(
            document, Path(output), book_meta=book_meta, lan=lan,
            table_render=table_render, latex_render=latex_render,
            inline_latex=inline_latex, aborted=aborted,
        )

    async def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        transformer: ChapterTransformer | SyncChapterTransformer,
        *, with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        ignore_errors: IgnoreFillErrorsChecker = False,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        async with temporary_directory("pdf-craft-translated-extraction-") as directory:
            translated = await self._translate_to_workspace(
                document, Path(directory) / "narrative", transformer,
                on_translation_event=on_translation_event,
            )
            if with_furniture:
                chapter_transformer = ChapterExtractionTransformer(transformer)
                if not isinstance(chapter_transformer.chapter_transformer, ChapterXMLTransformer):
                    raise ValueError("with_furniture=True requires a ChapterXMLTransformer")
                translated = await FurnitureExtractionTransformer(
                    _furniture_transformer_for(chapter_transformer.chapter_transformer),
                )._transform_to_workspace_async(translated, Path(directory) / "translated")
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
        source_path = Path(source)
        output_path = Path(output)
        configured_handler = self._pdf.pdf_handler if self._pdf else None
        async with _process_ignore_errors_checker(ignore_errors) as worker_checker:
            if configured_handler is None:
                await QT_DOMAIN.run(
                    _patch_pdf_sync,
                    source_path, document, output_path, None, worker_checker,
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
                await QT_DOMAIN.run(
                    _patch_pdf_sync,
                    source_path, document, output_path,
                    materialized_handler, worker_checker,
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
        translator: ChapterTransformer | SyncChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
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
        translator: ChapterTransformer | SyncChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
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
        footnotes = options.footnotes
        return await PDFExtractor(self._pdf_engine())._extract_to_workspace_async(
            Path(source), analysing_path,
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

    async def _translate_to_workspace(
        self, extraction: PDFCraftExtraction, output_path: Path,
        transformer: ChapterTransformer | SyncChapterTransformer, *,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
    ) -> PDFCraftExtraction:
        extraction_transformer = ChapterExtractionTransformer(transformer, mode=submit)
        return await extraction_transformer._transform_to_workspace_async(
            extraction,
            output_path,
            on_translation_event=on_translation_event,
            emit_translation_events=True,
        )

    def _sync_pdf_handler(self) -> PDFHandler | None:
        if self._pdf is None or self._pdf.pdf_handler is None:
            return None
        handler = self._pdf.pdf_handler
        if inspect.iscoroutinefunction(handler.open):
            return _AsyncPDFHandlerBridge(
                asyncio.get_running_loop(), cast(AsyncPDFHandler, handler),
            )
        return cast(PDFHandler, handler)

    def _pdf_engine(self):
        if self._engine is not None:
            return self._engine
        if self._pdf is None:
            raise ValueError("PDF extraction requires PDFCraft(pdf=PDFOptions(...))")
        from .transform import PDFExtractionEngine
        return PDFExtractionEngine(
            models_cache_path=self._pdf.models_cache_path,
            pdf_handler=self._sync_pdf_handler(),
            local_only=self._pdf.local_only,
            ocr=self._pdf.ocr,
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


class _AsyncPDFDocumentBridge:
    """Expose an async PDF document to the synchronous OCR worker."""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, document: AsyncPDFDocument,
    ) -> None:
        self._loop = loop
        self._document = document

    def _wait(self, awaitable):
        return asyncio.run_coroutine_threadsafe(awaitable, self._loop).result()

    @property
    def pages_count(self) -> int:
        return self._wait(self._document.pages_count())

    def metadata(self):
        return self._wait(self._document.metadata())

    def page_size(self, page_index: int) -> tuple[float, float]:
        return self._wait(self._document.page_size(page_index))

    def render_page(self, page_index: int, dpi: int):
        return self._wait(self._document.render_page(page_index, dpi))

    def close(self) -> None:
        self._wait(self._document.close())


class _AsyncPDFHandlerBridge:
    def __init__(
        self, loop: asyncio.AbstractEventLoop, handler: AsyncPDFHandler,
    ) -> None:
        self._loop = loop
        self._handler = handler

    def open(self, pdf_path: Path) -> PDFDocument:
        document = asyncio.run_coroutine_threadsafe(
            self._handler.open(pdf_path), self._loop,
        ).result()
        return cast(PDFDocument, _AsyncPDFDocumentBridge(self._loop, document))


@dataclass(frozen=True)
class _MaterializedPDFHandler:
    """Serializable PDF handler whose page rasters live in a temp workspace."""

    root: Path
    count: int
    document_metadata: PDFDocumentMetadata
    page_sizes: dict[int, tuple[float, float]]
    dpi: int

    def open(self, pdf_path: Path) -> PDFDocument:
        del pdf_path
        return _MaterializedPDFDocument(
            self.root, self.count, self.document_metadata, self.page_sizes, self.dpi,
        )


class _MaterializedPDFDocument:
    def __init__(
        self,
        root: Path,
        count: int,
        document_metadata: PDFDocumentMetadata,
        page_sizes: dict[int, tuple[float, float]],
        dpi: int,
    ) -> None:
        self._root = root
        self._count = count
        self._metadata = document_metadata
        self._page_sizes = page_sizes
        self._dpi = dpi

    @property
    def pages_count(self) -> int:
        return self._count

    def metadata(self) -> PDFDocumentMetadata:
        return self._metadata

    def page_size(self, page_index: int) -> tuple[float, float]:
        return self._page_sizes[page_index]

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        if dpi != self._dpi:
            raise ValueError(
                f"materialized PDF page uses {self._dpi} DPI, requested {dpi} DPI"
            )
        image = Image.open(self._root / f"page-{page_index}.png")
        image.load()
        return image

    def close(self) -> None:
        return None


async def _materialize_pdf_handler(
    handler: PDFHandler | AsyncPDFHandler,
    source: Path,
    page_indexes: tuple[int, ...],
    dpi: int,
    root: Path,
) -> _MaterializedPDFHandler:
    if not inspect.iscoroutinefunction(handler.open):
        return await IO_DOMAIN.run(
            _materialize_sync_pdf_handler,
            cast(PDFHandler, handler), source, page_indexes, dpi, root,
        )

    document = await cast(AsyncPDFHandler, handler).open(source)
    try:
        count = await document.pages_count()
        metadata = await document.metadata()
        page_sizes = {
            page_index: await document.page_size(page_index)
            for page_index in range(1, count + 1)
        }
        for page_index in page_indexes:
            if not 1 <= page_index <= count:
                continue
            image = await document.render_page(page_index, dpi)
            await IO_DOMAIN.run(
                _save_and_close_page_image, image, root / f"page-{page_index}.png",
            )
    finally:
        await document.close()
    return _MaterializedPDFHandler(root, count, metadata, page_sizes, dpi)


def _materialize_sync_pdf_handler(
    handler: PDFHandler,
    source: Path,
    page_indexes: tuple[int, ...],
    dpi: int,
    root: Path,
) -> _MaterializedPDFHandler:
    document = handler.open(source)
    try:
        count = document.pages_count
        metadata = document.metadata()
        page_sizes = {
            page_index: document.page_size(page_index)
            for page_index in range(1, count + 1)
        }
        for page_index in page_indexes:
            if not 1 <= page_index <= count:
                continue
            _save_and_close_page_image(
                document.render_page(page_index, dpi), root / f"page-{page_index}.png",
            )
    finally:
        document.close()
    return _MaterializedPDFHandler(root, count, metadata, page_sizes, dpi)


def _save_and_close_page_image(image: Image.Image, path: Path) -> None:
    try:
        image.save(path, format="PNG")
    finally:
        image.close()


def _patch_pdf_sync(
    source: Path,
    extraction: PDFCraftExtraction,
    output: Path,
    pdf_handler: PDFHandler | None,
    ignore_errors: IgnoreFillErrorsChecker,
) -> None:
    extraction._validate()
    if not _ignore_errors_requested(ignore_errors):
        _validate_extraction_for_pdf(source, extraction)
    PDFTranslationPipeline(pdf_handler=pdf_handler).patch(
        source, output, extraction, ignore_errors=ignore_errors,
    )


@dataclass(frozen=True)
class _RemoteIgnoreErrorsChecker:
    """Pickle-safe proxy for a caller-owned fill recovery predicate."""

    address: Any
    authkey: bytes

    def __call__(self, error: Exception) -> bool:
        connection = Client(self.address, authkey=self.authkey)
        try:
            connection.send(_pickle_safe_exception(error))
            status, value = connection.recv()
        finally:
            connection.close()
        if status == "error":
            if isinstance(value, BaseException):
                raise value
            raise RuntimeError(f"ignore_errors callback failed: {value}")
        return bool(value)


class _IgnoreErrorsCheckerServer:
    """Serve a local predicate to an isolated process without pickling it."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        checker: Callable[[Exception], bool],
    ) -> None:
        self._loop = loop
        self._checker = checker
        self._authkey = secrets.token_bytes(32)
        self._listener = Listener(("127.0.0.1", 0), authkey=self._authkey)
        self._thread = Thread(
            target=self._serve,
            name="pdf-craft-ignore-errors",
            daemon=True,
        )
        self._thread.start()

    def proxy(self) -> _RemoteIgnoreErrorsChecker:
        return _RemoteIgnoreErrorsChecker(self._listener.address, self._authkey)

    def _serve(self) -> None:
        while True:
            try:
                connection = self._listener.accept()
            except OSError:
                return
            try:
                error = connection.recv()
                if error is None:
                    return
                if not isinstance(error, Exception):
                    error = RuntimeError(str(error))
                future = asyncio.run_coroutine_threadsafe(
                    _invoke_ignore_errors_checker(self._checker, error),
                    self._loop,
                )
                try:
                    response = ("ok", future.result())
                except BaseException as callback_error:  # pylint: disable=broad-exception-caught
                    response = ("error", _pickle_safe_exception(callback_error))
                connection.send(response)
            except (EOFError, OSError):
                pass
            finally:
                connection.close()

    def close(self) -> None:
        try:
            connection = Client(self._listener.address, authkey=self._authkey)
            try:
                connection.send(None)
            finally:
                connection.close()
        except (ConnectionError, OSError):
            pass
        self._thread.join(timeout=5)
        self._listener.close()
        if self._thread.is_alive():
            raise RuntimeError("ignore_errors callback server did not stop")


async def _invoke_ignore_errors_checker(
    checker: Callable[[Exception], bool],
    error: Exception,
) -> bool:
    result = checker(error)
    if inspect.isawaitable(result):
        result = await cast(Awaitable[bool], result)
    return bool(result)


def _pickle_safe_exception(error: BaseException) -> BaseException:
    try:
        pickle.dumps(error, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # pylint: disable=broad-exception-caught
        return RuntimeError(f"{type(error).__module__}.{type(error).__qualname__}: {error}")
    return error


@asynccontextmanager
async def _process_ignore_errors_checker(
    checker: IgnoreFillErrorsChecker,
) -> AsyncIterator[IgnoreFillErrorsChecker]:
    if not callable(checker):
        yield checker
        return
    server = await IO_DOMAIN.run(
        _IgnoreErrorsCheckerServer,
        asyncio.get_running_loop(),
        checker,
    )
    try:
        yield server.proxy()
    finally:
        await IO_DOMAIN.run(server.close)


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


def _furniture_transformer_for(
    transformer: ChapterTransformer | SyncChapterTransformer,
) -> FurnitureXMLTransformer:
    """Reuse the XML translation runtime for the optional furniture pass."""
    if not isinstance(transformer, ChapterXMLTransformer):
        raise ValueError("with_furniture=True requires a ChapterXMLTransformer")
    return transformer._furniture_transformer()


def _transformer_target_language(
    transformer: ChapterTransformer | SyncChapterTransformer,
) -> str | None:
    if isinstance(transformer, ChapterXMLTransformer):
        return transformer.target_language
    value = getattr(transformer, "target_language", None)
    return value if isinstance(value, str) else None


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
