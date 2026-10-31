# pylint: disable=unused-argument
"""Public facade that composes pdf-craft's independent components."""

from pdf_craft.usage import UsageCollector
from collections.abc import Mapping
from pdf_craft.pdf.backend import pdf_backend_scope, poppler_path_for
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation

# Internal workspace methods are intentionally shared only inside pdf-craft.
# pylint: disable=protected-access

import secrets
from collections.abc import Callable, Container
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path
from xml.etree.ElementTree import Element
from typing import Any, AsyncIterator, cast

from epub_generator import BookMeta, LaTeXRender, TableRender

from .document import PDFCraftExtraction, RenderMode, TranslationInfo, resolve_translation
from .document.package import validate_translation_id
from .document.metadata import MetadataCallback
from .document.paragraph import ParagraphContent, SourceRegion, PDFParagraphContent
from .pipeline.epub.events import EpubFragmentContent
from .document.access import ExtractionContext, PageGeometry, page_geometries, asset_path
from .extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from .extractor.chapter.reader import create_chapters_reader
from .extractor import PDFExtractor
from .extractor.progress import extraction_facade
from .refinement import Refinement
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf.handler import _materialize_pdf_handler
from .pdf import (
    AsyncPDFHandler,
    DeepSeekOCRSize,
    OCRImageURLResolver,
    PDFHandler,
)
from .pipeline.epub import translate_epub as run_epub_translation
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import (
    AnchoredContentExtractionTransformer,
    AnchoredContentTransformer,
    SubmitKind,
    ProgressEvent,
    XMLTranslator,
)
from .transformer.narrative_xml import NarrativeXMLTransformer
from .transformer.pcex_progress import report_pcex_translation
from .transformer.progress import translation_operation
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
    """Controls for one PDF extraction run.

    on_body/on_footnote deliver closed page-processed XML incrementally,
    after optional local refinement and before final TOC organization.
    Delivery does not indicate operation success or mirror final chapter levels.
    """

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
    footnotes: bool = False
    refinement: Refinement | None = None
    aborted: AbortedCheck = lambda: False
    on_extraction_event: Callable[[ProgressEvent], object] | None = None
    on_body: Callable[[Element], object] | None = None
    on_footnote: Callable[[Element], object] | None = None
    context: ExtractionContext | None = None
    on_metadata: MetadataCallback | None = None

    def __post_init__(self) -> None:
        from .pdf.png import validate_image_limit
        validate_image_limit(self.max_page_image_file_size)
        if not isinstance(self.footnotes, bool):
            raise TypeError("footnotes must be bool")
        if self.refinement is not None and not isinstance(self.refinement, Refinement):
            raise TypeError("refinement must be Refinement or None")


class AsyncPDFCraft:
    """Async-first facade for extraction, rendering, and translation.

    Native async orchestration owns callbacks and cancellation. Synchronous
    native third-party operations run in dedicated domains while the event loop
    owns business orchestration and executable extensions.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, retry_policy: RetryPolicy | None = None,
                 error_policy: ErrorPolicyInput = None, _engine=None) -> None:
        self._retry_policy = retry_policy or RetryPolicy()
        self._error_policy = error_policy
        self._pdf = pdf
        self._engine = _engine

    def _backend_poppler_path(self) -> Path | None:
        """Share the native adapter's tool configuration with nested readers."""
        return poppler_path_for(self._engine)

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

    async def pcex_words_count(self, source: PDFCraftExtraction | PathLike | str, *,
                               with_anchored: bool = True, with_furniture: bool = False) -> int:
        from .size import pcex_words_count
        return await IO_DOMAIN.run(pcex_words_count, source, with_anchored=with_anchored,
                                   with_furniture=with_furniture)

    async def epub_words_count(self, source: PathLike | str, *,
                               submit: SubmitKind = SubmitKind.REPLACE) -> int:
        from .size import epub_words_count
        return await IO_DOMAIN.run(epub_words_count, source, submit=submit)

    async def read_pages(self, extraction: PDFCraftExtraction | PathLike | str) -> dict[int, PageGeometry]:
        """Read public source page dimensions, DPI and coordinate space once."""
        document = await _ensure_extraction_async(extraction)
        def read():
            from .document.package import _read_pages
            with document._materialize() as paths:
                dpi, sizes = _read_pages(paths.pages)
                return page_geometries(sizes, dpi)
        return await IO_DOMAIN.run(read)

    async def read_asset(self, extraction: PDFCraftExtraction | PathLike | str, asset_hash: str) -> bytes:
        """Return a copy of a resource, independent of archive materialization."""
        document = await _ensure_extraction_async(extraction)
        def read():
            with document._materialize() as paths:
                return asset_path(paths.assets, asset_hash).read_bytes()
        return await IO_DOMAIN.run(read)

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

    @policy_operation
    @pdf_backend_scope
    async def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> PDFCraftExtraction:
        extraction, _ = await self.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path,
        )
        return extraction

    @policy_operation
    @pdf_backend_scope
    @extraction_facade
    async def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
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
                includes_footnotes=footnotes,
                includes_furniture=options.includes_furniture,
                extract_book_metadata=options.extract_book_metadata,
                metadata_llm=options.metadata_llm,
                generate_plot=options.generate_plot,
                toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
                refinement=options.refinement,
                aborted=options.aborted,
                on_extraction_event=options.on_extraction_event,
                **({"context": options.context} if options.context is not None else {}),
                **({"on_body": options.on_body} if options.on_body is not None else {}),
                **({"on_footnote": options.on_footnote} if options.on_footnote is not None else {}),
                **(cast(dict[str, Any], {"on_metadata": options.on_metadata}) if options.on_metadata is not None else {}),
            )

    @policy_operation
    async def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
        translator: XMLTranslator | None = None, window: int = 1,
        with_anchored: bool = True,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        await MarkdownRenderer().render(
            document, Path(output),
            Path(assets_path) if assets_path is not None else None,
            mode=mode, translation_id=translation_id,
            aborted=aborted, on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
            translator=translator, window=window, with_anchored=with_anchored,
        )

    @policy_operation
    @translation_operation
    async def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_anchored: bool = True,
        with_furniture: bool = False,
        translation_id: str | None = None,
        target_language: str | None = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
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
                on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
            )
            return await layered._export_async(target)

    @policy_operation
    async def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer, *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> PDFCraftExtraction:
        document = await _ensure_extraction_async(extraction)
        return await AnchoredContentExtractionTransformer(
            transformer,
        ).transform(
            document, Path(output_path),
        )

    @policy_operation
    async def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, label_language: str = "en",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
        translator: XMLTranslator | None = None, window: int = 1,
        with_anchored: bool = True,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> None:
        """Generate EPUB; label_language controls fixed labels only.

        Exact zh/en select built-in labels; all other values use English.
        This option neither selects nor limits the document/translation language.
        """
        document = await _ensure_extraction_async(extraction)
        await EpubRenderer().render(
            document, Path(output), book_meta=book_meta, label_language=label_language,
            table_render=table_render, latex_render=latex_render,
            inline_latex=inline_latex, mode=mode,
            translation_id=translation_id, aborted=aborted,
            on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
            translator=translator, window=window, with_anchored=with_anchored,
        )

    @policy_operation
    @pdf_backend_scope
    @translation_operation
    async def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_furniture: bool = False,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_body: Callable[[PDFParagraphContent], object] | None = None,
        on_footnote: Callable[[PDFParagraphContent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> None:
        if isinstance(translator, XMLTranslator):
            await self.patch_pdf_with_extraction(source, extraction, output, translator=translator,
                window=window, error_policy=error_policy, on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
                with_furniture=with_furniture, on_translation_event=on_translation_event)
            return
        document = await _ensure_extraction_async(extraction)
        async with temporary_directory("pdf-craft-translated-extraction-") as directory:
            translated = await self._translate_to_workspace(
                document, Path(directory), translator,
                window=window,
                with_anchored=False,
                with_furniture=with_furniture,
                on_translation_event=on_translation_event, on_metadata=on_metadata,
            )
            await self.patch_pdf_with_extraction(
                source, translated, output, error_policy=error_policy,
                on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
            )

    @policy_operation
    @pdf_backend_scope
    @translation_operation
    async def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        *, translation_id: str | None = None,
        translator: XMLTranslator | None = None, window: int = 1,
        on_body: Callable[[PDFParagraphContent], object] | None = None,
        on_footnote: Callable[[PDFParagraphContent], object] | None = None,
        with_furniture: bool = False,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> None:
        document = await _ensure_extraction_async(extraction)
        if translator is not None and translation_id is not None:
            raise ValueError("translation_id cannot be combined with translator")
        from .pipeline.pdf.content import patch_content
        if not _error_policy_requested(error_policy):
            await IO_DOMAIN.run(_validate_extraction_for_pdf, Path(source), document)
        await patch_content(Path(source), document, Path(output), translator=translator,
                            translation_id=translation_id, window=window, error_policy=error_policy,
                            on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata,
                            pdf_handler=self._pdf.pdf_handler if self._pdf else None, with_furniture=with_furniture,
                            on_translation_event=on_translation_event)

    @policy_operation
    @translation_operation
    async def translate_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        target_language: str, submit: SubmitKind,
        user_prompt: str | None = None,
        max_group_tokens: int = 2600,
        llm: LLM | None = None,
        translation_llm: LLM | None = None, fill_llm: LLM | None = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        executor: AsyncExecutor | None = None,
        translation_executor: AsyncExecutor | None = None,
        fill_executor: AsyncExecutor | None = None,
        window: int = 1,
        on_body: Callable[[EpubFragmentContent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> None:
        await run_epub_translation(
            source, output,
            target_language=target_language,
            submit=submit,
            user_prompt=user_prompt,
            max_group_tokens=max_group_tokens,
            llm=llm,
            translation_llm=translation_llm,
            fill_llm=fill_llm,
            on_translation_event=on_translation_event,
            executor=executor,
            translation_executor=translation_executor,
            fill_executor=fill_executor,
            window=window,
            on_body=on_body,
            on_metadata=on_metadata,
        )

    @policy_operation
    @pdf_backend_scope
    @translation_operation
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
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
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
                        on_translation_event=on_translation_event, on_metadata=on_metadata,
                    )
                    await self.render_markdown(
                        document, output, assets_path, aborted=options.aborted,
                        mode=_submission_render_mode(submit),
                        translation_id=(await IO_DOMAIN.run(document._translations))[-1].id,
                    )
            else:
                await self.render_markdown(document, output, assets_path, aborted=options.aborted)
        return metering

    @policy_operation
    @pdf_backend_scope
    @translation_operation
    async def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, label_language: str = "en",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: XMLTranslator | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        window: int = 1,
        with_anchored: bool = True,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> OCRTokensMetering:
        """Generate EPUB; label_language controls fixed labels only.

        Exact zh/en select built-in labels; all other values use English.
        This option neither selects nor limits the document/translation language.
        """
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
                        on_translation_event=on_translation_event, on_metadata=on_metadata,
                    )
                    await self.render_epub(
                        document, output, book_meta=book_meta,
                        label_language=label_language,
                        table_render=table_render, latex_render=latex_render,
                        inline_latex=inline_latex, aborted=options.aborted,
                        mode=_submission_render_mode(submit),
                        translation_id=(await IO_DOMAIN.run(document._translations))[-1].id,
                    )
            else:
                await self.render_epub(
                    document, output, book_meta=book_meta, label_language=label_language,
                    table_render=table_render, latex_render=latex_render,
                    inline_latex=inline_latex, aborted=options.aborted,
                )
        return metering

    @extraction_facade
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
                includes_footnotes=footnotes,
                includes_furniture=options.includes_furniture,
                extract_book_metadata=options.extract_book_metadata,
                metadata_llm=options.metadata_llm,
                generate_plot=options.generate_plot,
                toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
                refinement=options.refinement,
                aborted=options.aborted, on_extraction_event=options.on_extraction_event,
                **({"context": options.context} if options.context is not None else {}),
                **({"on_body": options.on_body} if options.on_body is not None else {}),
                **({"on_footnote": options.on_footnote} if options.on_footnote is not None else {}),
                **(cast(dict[str, Any], {"on_metadata": options.on_metadata}) if options.on_metadata is not None else {}),
            )

    async def _translate_layer_to_workspace(
        self, extraction: PDFCraftExtraction, workspace: Path,
        translator: XMLTranslator, *, window: int = 1,
        with_anchored: bool = True, with_furniture: bool = False,
        translation_id: str | None = None, target_language: str | None = None,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None, aborted=lambda: False,
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
            metadata_language=language,
            on_translation_event=on_translation_event,
            on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata, aborted=aborted,
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
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None, aborted=lambda: False,
    ) -> PDFCraftExtraction:
        """Materialize replacement-only root content for PDF writeback."""
        translated, overlay = await self._translate_replacement_to_workspace(
            extraction, workspace, translator, window=window,
            with_anchored=with_anchored, with_furniture=with_furniture,
            on_translation_event=on_translation_event,
            on_body=on_body, on_footnote=on_footnote, on_metadata=on_metadata, aborted=aborted,
        )
        if overlay:
            async with worker_context(translated._materialize()) as paths:
                translated = await IO_DOMAIN.run(
                    _apply_metadata_overlay_to_workspace, paths.root, overlay,
                )
        return translated

    @report_pcex_translation
    async def _translate_replacement_to_workspace(
        self, extraction: PDFCraftExtraction, workspace: Path,
        translator: XMLTranslator, *,
        window: int = 1,
        with_anchored: bool = True,
        with_furniture: bool = False,
        on_translation_event: Callable[[ProgressEvent], object] | None = None,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None, aborted=lambda: False, metadata_language=None,
    ) -> tuple[PDFCraftExtraction, dict]:
        narrative_transformer = NarrativeXMLTransformer(
            translator, mode=SubmitKind.REPLACE, window=window,
        )
        async def translate_metadata():
            return await narrative_transformer.translate_metadata(
                await IO_DOMAIN.run(extraction._document_metadata),
                on_metadata=on_metadata, target_language=metadata_language)
        metadata_overlay = {}
        if narrative_transformer.supports_stream:
            from .transformer.content import translate_workspace
            translated = await translate_workspace(
                extraction, workspace / "narrative", narrative_transformer,
                window=window, with_anchored=with_anchored, with_furniture=with_furniture,
                metadata=translate_metadata, metadata_result=metadata_overlay,
                on_body=on_body, on_footnote=on_footnote, aborted=aborted,
                on_translation_event=on_translation_event, emit_translation_events=True,
            )
        else:
            metadata_overlay = await translate_metadata()
            # Preserve custom task-translator extensions whose contract is
            # whole-element/batch, rather than silently changing that contract.
            translated = await ChapterExtractionTransformer(
                narrative_transformer, mode=SubmitKind.REPLACE,
            )._transform_to_workspace_async(
                extraction, workspace / "narrative",
                on_translation_event=on_translation_event, emit_translation_events=True,
            )
            if with_anchored:
                translated = await AnchoredContentExtractionTransformer(
                    narrative_transformer._anchored_transformer(),
                )._transform_to_workspace_async(translated, workspace / "anchored")
            if on_body is not None or on_footnote is not None:
                from .renderer.paragraph import notify_extraction
                await notify_extraction(extraction, translated, on_body, on_footnote)
        if with_furniture and not narrative_transformer.supports_stream:
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
            native_result = None
            async def native_metadata(value):
                nonlocal native_result
                from .extractor.metadata import merge_ocr_and_pdf_metadata
                from .extractor.progress import analysis_unit
                from .progress import UnitKind
                from .document.metadata import MetadataContent
                from .runtime import invoke_callback
                async with analysis_unit(UnitKind.METADATA):
                    native_result = merge_ocr_and_pdf_metadata(None, value)
                    if options.on_metadata is not None:
                        await invoke_callback(options.on_metadata, MetadataContent(native_result))
            materialized = await _materialize_pdf_handler(
                handler, source, options.page_indexes,
                options.dpi if options.dpi is not None else 300, directory,
                on_metadata=native_metadata if not options.extract_book_metadata else None,
            )
            engine = self._pdf_engine(materialized)
            engine._native_metadata_result = native_result
            yield PDFExtractor(engine)

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


def _validate_extraction_for_pdf(source: Path, extraction: PDFCraftExtraction) -> None:
    """Fail before patching when extraction geometry cannot match the PDF."""
    page_sizes = extraction._page_pixel_sizes()
    if not page_sizes:
        raise ValueError("PDFCraftExtraction is missing page geometry required for PDF patching")
    from .pdf.poppler import PopplerDocument
    from .pdf.backend import current_poppler_path
    document = PopplerDocument(source, current_poppler_path())
    try:
        page_count = document.pages_count
    finally:
        document.close()
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


def _error_policy_requested(checker: ErrorPolicyInput) -> bool:
    """Defer page-addressable validation when a fill recovery policy exists."""
    from ._policy import current
    from .policies import ErrorPolicy, FailureCategory
    checker = current().errors if checker is OMITTED else checker
    if isinstance(checker, ErrorPolicy):
        checker = checker.decision(FailureCategory.PDF_FILL)
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
