# pylint: disable=unused-argument
# pylint: disable=protected-access

from pdf_craft.pdf.backend import pdf_backend_scope, poppler_path_for
from .policies import ErrorPolicyInput
from ._policy import decision_bridge, OMITTED, policy_operation

import asyncio
from collections.abc import Callable, Container
from contextlib import aclosing
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path

from .error import (
    NoUsableOCRPagesError,
    PDFError,
)
from .footnote import FootnoteRefinement
from .concurrency import AsyncExecutor, fatal_error_boundary
from .jev import JEVRuntime
from .llm import LLM, runtime_for
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import (
    DeepSeekOCRSize, OCR, OCRImageURLResolver, OCREvent, OCREventKind,
    PDFHandler,
)
from .pdf.furniture import write_furnitures
from .runtime import (
    IO_DOMAIN, OCR_DOMAIN, callback_bridge, invoke_callback, run_cancellable,
    run_subprocess, temporary_directory,
)
from .document.metadata import MetadataContent
from .extractor.metadata import extract_book_metadata_from_ocr, merge_ocr_and_pdf_metadata
from .extractor.chapter import (
    generate_chapter_files,
    prepare_chapter_analysis,
)
from .extractor.chapter.page_repair import JevLlmRepairProcessor
from .extractor.toc import TocInfo, analyse_toc
from .document import (
    DocumentMetadata,
    ExtractionPaths,
    PDFCraftExtraction,
    write_manifest,
    write_pages,
)


@dataclass(frozen=True)
class _ExtractionDraft:
    pdf_path: Path
    extraction_path: Path
    extraction_paths: ExtractionPaths
    pages_path: Path
    chapters_path: Path
    toc_path: Path
    toc: TocInfo
    cover_path: Path | None
    document_metadata: DocumentMetadata | None
    metering: OCRTokensMetering
    dpi: int | None
    includes_furniture: bool
    native_pdf_text: bytes | None
    native_pdf_text_prepared: bool


class PDFExtractionEngine:
    """Internal PDF extraction engine used by the :class:`PDFCraft` facade."""

    def __init__(
        self,
        models_cache_path: PathLike | str | None = None,
        pdf_handler: PDFHandler | None = None,
        local_only: bool = False,
        ocr: OCRConfig | None = None,
        ocr_executor: AsyncExecutor | None = None,
        ocr_image_url_resolver: OCRImageURLResolver | None = None,
    ) -> None:
        self._poppler_path = poppler_path_for(pdf_handler)
        self._ocr = OCR(
            ocr=ensure_ocr_config(ocr, models_cache_path, local_only),
            pdf_handler=pdf_handler,
        )
        if self._ocr.is_vendor and ocr_executor is None:
            raise ValueError("Vendor OCR requires an explicit OCR executor")
        self._ocr_executor = ocr_executor
        self._ocr_image_url_resolver = ocr_image_url_resolver

    def _backend_poppler_path(self) -> Path | None:
        """Share the native adapter's tool configuration with nested readers."""
        return getattr(self, "_poppler_path", None)

    async def predownload(self, revision: str | None = None) -> None:
        await OCR_DOMAIN.run(self._ocr.predownload, revision)

    async def load_models(self) -> None:
        await OCR_DOMAIN.run(self._ocr.load_models)

    @pdf_backend_scope
    @fatal_error_boundary
    async def extract_package_async(self, **kwargs):
        """Own disk queues until producers and callback consumers have unwound."""
        from .extractor.chapter.live import LiveExtraction
        on_metadata = kwargs.pop("on_metadata", None)
        if kwargs.get("extract_book_metadata", False) and kwargs.get("metadata_llm") is None:
            raise ValueError("extract_book_metadata=True requires metadata_llm")
        if on_metadata is not None and not kwargs.get("extract_book_metadata", False):
            metadata = merge_ocr_and_pdf_metadata(
                None, await self._read_pdf_metadata_async(kwargs["pdf_path"]),
            )
            await invoke_callback(on_metadata, MetadataContent(metadata))
            kwargs["document_metadata"] = metadata
            on_metadata = None  # Already delivered the final native-only view.
        context = kwargs.pop("context", None)
        on_body = kwargs.pop("on_body", None)
        on_footnote = kwargs.pop("on_footnote", None)
        async with temporary_directory("pdf-craft-live-extraction-") as directory:
            if context is not None:
                context._bind(kwargs["analysing_path"] / "extraction" / "assets")
            live = LiveExtraction(directory, kwargs["analysing_path"] / "ocr",
                                  kwargs["includes_footnotes"], on_body, on_footnote)
            callback = kwargs.get("on_ocr_event")
            async def event(value):
                if context is not None:
                    context._update(self._ocr.last_page_pixel_sizes, kwargs["dpi"] or 300)
                await live.event(value)
                await invoke_callback(callback, value)
            kwargs["on_ocr_event"] = event
            try:
                if context is not None:
                    sizes = await IO_DOMAIN.run(OCR._load_page_pixel_sizes, self._ocr,
                                               kwargs["analysing_path"] / "ocr" / "page_pixel_sizes.json")
                    context._update(sizes, kwargs["dpi"] or 300)
                return await self._extract_package_async(**kwargs, live=live, on_metadata=on_metadata)
            finally:
                await live.cancel()
                if context is not None:
                    context._unbind()

    async def _extract_package_async(self, **kwargs):
        """Run optional footnote refinement between two OCR-domain phases."""

        live = kwargs.pop("live", None)
        on_metadata = kwargs.pop("on_metadata", None)
        footnote_refinement = kwargs.pop("footnote_refinement", None)
        kwargs["error_policy"] = decision_bridge(asyncio.get_running_loop(), OMITTED)
        event_callback = kwargs.get("on_ocr_event")
        original_aborted = kwargs.get("aborted")
        if self._ocr.is_vendor and self._ocr_executor is not None:
            ocr_metering = OCRTokensMetering(input_tokens=0, output_tokens=0)
            event_stream = self._ocr.recognize_vendor(
                self._ocr_executor,
                image_url_resolver=self._ocr_image_url_resolver,
                on_preparation_event=event_callback,
                pdf_path=kwargs["pdf_path"],
                asset_path=kwargs["analysing_path"] / "extraction" / "assets",
                ocr_path=kwargs["analysing_path"] / "ocr",
                ocr_size=kwargs["ocr_size"],
                dpi=kwargs["dpi"],
                max_page_image_file_size=kwargs["max_page_image_file_size"],
                includes_footnotes=kwargs["includes_footnotes"],
                error_policy=OMITTED,
                plot_path=(kwargs["analysing_path"] / "plots")
                if kwargs["generate_plot"] else None,
                cover_path=(kwargs["analysing_path"] / "extraction" / "cover.png")
                if kwargs["includes_cover"] else None,
                aborted=kwargs["aborted"],
                page_indexes=kwargs["page_indexes"]
                if kwargs["page_indexes"] is not None else range(1, 2**31),
                max_tokens=kwargs["max_tokens"],
                max_output_tokens=kwargs["max_output_tokens"],
            )
            async with aclosing(event_stream):
                async for event in event_stream:
                    ocr_metering.input_tokens += event.input_tokens
                    ocr_metering.output_tokens += event.output_tokens
                    await invoke_callback(event_callback, event)
            kwargs["skip_ocr"] = True
            kwargs["ocr_metering"] = ocr_metering
        else:
            kwargs["on_ocr_event"] = callback_bridge(
                asyncio.get_running_loop(), event_callback,
            )
        if footnote_refinement is not None and not isinstance(footnote_refinement, FootnoteRefinement):
            raise TypeError("footnote_refinement must be FootnoteRefinement")
        draft = await run_cancellable(
            OCR_DOMAIN,
            lambda aborted: self._prepare_extraction(**{**kwargs, "aborted": aborted}),
            original_aborted=original_aborted,
        )
        if live is not None:
            await live.finish()
        from .extractor.progress import active_extraction
        progress = active_extraction()
        if progress is not None:
            await progress.cached_pages()
        draft = await self._resolve_llm_stages(draft, kwargs, on_metadata)
        chapter_analysis = None
        try:
            if live is not None and live.analysis_error is not None:
                raise live.analysis_error
            repaired_pages = None
            if footnote_refinement is not None:
                chapter_analysis = await run_cancellable(
                    OCR_DOMAIN,
                    lambda _aborted: prepare_chapter_analysis(draft.pages_path, draft.toc),
                    original_aborted=original_aborted,
                )
                repaired_pages = await self._refine_pages(chapter_analysis, footnote_refinement, draft)
            return await self._finish_with_toc_recovery(
                draft, analysed_pages=repaired_pages,
                resolved_database=(live.database if live is not None and repaired_pages is None
                                   and not draft.toc.page_indexes else None),
                aborted=original_aborted,
            )
        except Exception as error:
            from .extractor.recovery import recover_chapters, recoverable_structure
            if not recoverable_structure(error):
                raise
            async with temporary_directory("pdf-craft-page-recovery-") as directory:
                recovered = await recover_chapters(
                    draft, self._ocr, error, directory,
                    analyses=chapter_analysis.pages if chapter_analysis is not None else (),
                    aborted=original_aborted or (lambda: False),
                )
                draft = replace(draft, toc=recovered.toc)
                from .common import save_xml
                from .extractor.toc import encode as encode_toc
                await IO_DOMAIN.run(save_xml, encode_toc(draft.toc), draft.toc_path)
                return await run_cancellable(
                    OCR_DOMAIN,
                    lambda aborted: self._finish_extraction(draft, aborted=aborted, prepared_chapters=recovered.chapters),
                    original_aborted=original_aborted,
                )

    async def _finish_with_toc_recovery(self, draft, *, analysed_pages, resolved_database, aborted):
        """Directory consistency is repaired before discarding usable pages."""
        from .common import save_xml
        from .document.validation import ChapterTocMismatch
        from .extractor.chapter.toc_identity import chapter_ids, retained_toc
        from .extractor.toc import encode as encode_toc
        from ._policy import recover
        from .policies import FailureCategory, FailureLocation

        async def finish(**options):
            return await run_cancellable(
                OCR_DOMAIN,
                lambda check: self._finish_extraction(draft, aborted=check, **options),
                original_aborted=aborted,
            )

        def targets(error):
            return tuple(FailureLocation('page', source=str(draft.pdf_path), page_indexes=(page,),
                                         page=self._ocr.page_geometry(page, draft.dpi or 300))
                         for page in error.page_indexes)

        try:
            return await finish(analysed_pages=analysed_pages, resolved_database=resolved_database)
        except ChapterTocMismatch as error:
            mismatch = error
        if analysed_pages is not None:
            # A structurally legal page edit can invalidate a global TOC anchor.
            # Withdraw the enhancement once, rather than declaring OCR unusable.
            affected = tuple(FailureLocation('page', source=str(draft.pdf_path),
                                              page_indexes=(page.page_index,),
                                              page=self._ocr.page_geometry(page.page_index, draft.dpi or 300))
                             for page in analysed_pages)
            await recover(mismatch, FailureCategory.ANALYSIS, 'chapter_toc', 'algorithm_result',
                          targets=targets(mismatch), affected=affected)
            try:
                return await finish()
            except ChapterTocMismatch as error:
                mismatch = error
        await recover(mismatch, FailureCategory.ANALYSIS, 'chapter_toc', 'chapter_toc',
                      targets=targets(mismatch))
        # Already generated content is retained exactly. Only unavailable TOC
        # anchors are removed, with caller approval; unexpected IDs still fail.
        identities = await IO_DOMAIN.run(chapter_ids, draft.chapters_path)
        toc = await IO_DOMAIN.run(retained_toc, draft.toc, identities)
        draft = replace(draft, toc=toc)
        await IO_DOMAIN.run(save_xml, encode_toc(toc), draft.toc_path)
        return await finish(prepared_chapters=draft.chapters_path)

    async def _refine_pages(self, chapter_analysis, footnote_refinement, draft):
        runtime = runtime_for(
            footnote_refinement.llm,
            footnote_refinement.resolved_llm_executor(),
            protocol_version="footnote-refinement-json-v1",
        )

        async def request(messages, index, maximum):
            return await runtime.request(
                messages,
                max_tokens=footnote_refinement.max_output_tokens,
                retry_index=index,
                retry_max=maximum,
            )

        async with JEVRuntime(
            footnote_refinement.jev,
            footnote_refinement.resolved_jev_executor(),
        ) as jev:
            processor = JevLlmRepairProcessor(
                jev.evaluate,
                request,
                threshold=footnote_refinement.risk_threshold,
                batch_evaluator=jev.evaluate_many,
            )
            from ._failure_sources import page_sources
            from .document.access import page_geometries
            with page_sources(page_geometries(chapter_analysis.page_pixel_sizes, draft.dpi or 300)):
                repaired_pages = await processor(
                    chapter_analysis.source_pages,
                    chapter_analysis.pages,
                    chapter_analysis.page_pixel_sizes,
                )

        return repaired_pages

    async def prepare_extract(
        self,
        *,
        pdf_path: Path,
        includes_furniture: bool = True,
        **_kwargs,
    ) -> dict[str, object]:
        """Fetch direct subprocess inputs before entering the OCR worker."""
        if not includes_furniture:
            return {}
        from .pdf.poppler import PopplerDocument
        from .pdf.backend import current_poppler_path
        command = PopplerDocument(pdf_path, current_poppler_path()).command("pdftotext")
        try:
            stdout, _ = await run_subprocess(
                command, "-bbox-layout", str(pdf_path), "-",
            )
        except (FileNotFoundError, RuntimeError):
            stdout = None
        return {
            "native_pdf_text": stdout,
            "native_pdf_text_prepared": True,
        }

    def _prepare_extraction(
        self,
        pdf_path: Path,
        analysing_path: Path,
        ocr_size: DeepSeekOCRSize,
        dpi: int | None,
        max_page_image_file_size: int | None,
        includes_cover: bool,
        includes_footnotes: bool,
        error_policy: ErrorPolicyInput,
        generate_plot: bool,
        toc_llm: LLM | None,  # pylint: disable=unused-argument
        toc_assumed: bool,  # pylint: disable=unused-argument
        aborted: AbortedCheck,
        max_tokens: int | None,
        max_output_tokens: int | None,
        on_ocr_event: Callable[[OCREvent], None],
        page_indexes: Container[int] | None = None,
        includes_furniture: bool = True,
        extract_book_metadata: bool = False,
        metadata_llm: LLM | None = None,
        native_pdf_text: bytes | None = None,
        native_pdf_text_prepared: bool = False,
        skip_ocr: bool = False,
        ocr_metering: OCRTokensMetering | None = None,
        document_metadata: DocumentMetadata | None = None,
    ):
        if extract_book_metadata and metadata_llm is None:
            raise ValueError("extract_book_metadata=True requires metadata_llm")
        extraction_path = analysing_path / "extraction"
        extraction_paths = ExtractionPaths.at(extraction_path)
        assets_path = extraction_paths.assets
        pages_path = analysing_path / "ocr"
        chapters_path = extraction_paths.chapters
        toc_path = extraction_paths.toc

        cover_path: Path | None = extraction_paths.cover if includes_cover else None
        plot_path: Path | None = analysing_path / "plots" if generate_plot else None
        metering = ocr_metering or OCRTokensMetering(input_tokens=0, output_tokens=0)
        usable_pages = 0
        failed_page_indexes: list[int] = []
        events = () if skip_ocr else self._ocr.recognize(
            pdf_path=pdf_path,
            asset_path=assets_path,
            ocr_path=pages_path,
            ocr_size=ocr_size,
            dpi=dpi,
            max_page_image_file_size=max_page_image_file_size,
            includes_footnotes=includes_footnotes,
            error_policy=error_policy,
            plot_path=plot_path,
            cover_path=cover_path,
            aborted=aborted,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
            page_indexes=page_indexes if page_indexes is not None else range(1, 2**31),
        )
        for event in events:
            on_ocr_event(event)
            metering.input_tokens += event.input_tokens
            metering.output_tokens += event.output_tokens
            if event.kind in (OCREventKind.COMPLETE, OCREventKind.SKIP):
                usable_pages += 1
            elif event.kind == OCREventKind.FAILED:
                failed_page_indexes.append(event.page_index)

        if failed_page_indexes and usable_pages == 0:
            raise NoUsableOCRPagesError(tuple(failed_page_indexes))

        # Read the unmodified page XML while it is still the direct OCR record.
        # Later TOC and chapter stages consume the same files, but must not define
        # what bibliographic evidence is available to this optional feature.
        return _ExtractionDraft(
            pdf_path=pdf_path,
            extraction_path=extraction_path,
            extraction_paths=extraction_paths,
            pages_path=pages_path,
            chapters_path=chapters_path,
            toc_path=toc_path,
            toc=TocInfo(content=[], page_indexes=[]),
            cover_path=cover_path,
            document_metadata=document_metadata,
            metering=metering,
            dpi=dpi,
            includes_furniture=includes_furniture,
            native_pdf_text=native_pdf_text,
            native_pdf_text_prepared=native_pdf_text_prepared,
        )

    async def _resolve_llm_stages(self, draft: _ExtractionDraft, kwargs: dict, on_metadata=None) -> _ExtractionDraft:
        from .extractor.progress import analysis_unit
        from .progress import UnitKind
        if kwargs['extract_book_metadata']:
            async with analysis_unit(UnitKind.METADATA):
                metadata = draft.document_metadata or await self._extract_book_metadata_async(
                    pdf_path=draft.pdf_path, pages_path=draft.pages_path,
                    enabled=True, metadata_llm=kwargs['metadata_llm'])
        else:
            metadata = draft.document_metadata
        if on_metadata is not None:
            await invoke_callback(on_metadata, MetadataContent(metadata or DocumentMetadata()))
        reused = await IO_DOMAIN.run(draft.toc_path.exists)
        async with analysis_unit(UnitKind.TOC, reused=reused):
            toc = await analyse_toc(
                pages_path=draft.pages_path, toc_path=draft.toc_path,
                toc_llm=kwargs['toc_llm'], toc_assumed=kwargs['toc_assumed'])
        return replace(draft, document_metadata=metadata, toc=toc)

    @policy_operation
    async def _extract_book_metadata_async(
        self,
        *,
        pdf_path: Path,
        pages_path: Path,
        enabled: bool,
        metadata_llm: LLM | None,
        error_policy: ErrorPolicyInput = OMITTED,
    ) -> DocumentMetadata | None:
        if not enabled:
            return None
        pdf_metadata = await self._read_pdf_metadata_async(pdf_path)
        ocr_metadata = None
        try:
            assert metadata_llm is not None
            ocr_metadata = await extract_book_metadata_from_ocr(pages_path, metadata_llm)
        except Exception as error:
            from ._policy import recover
            from .policies import FailureCategory
            from ._failure_sources import ocr_locations
            targets = await IO_DOMAIN.run(ocr_locations, pages_path, 12)
            await recover(error, FailureCategory.ANALYSIS, 'book_metadata', 'pdf_metadata', targets=getattr(error, 'failure_targets', targets))
        return merge_ocr_and_pdf_metadata(ocr_metadata, pdf_metadata)

    async def _read_pdf_metadata_async(self, pdf_path):
        try:
            return await IO_DOMAIN.run(self._ocr.metadata, pdf_path)
        except PDFError as error:
            from ._policy import recover
            from .policies import FailureCategory, FailureLocation
            await recover(error, FailureCategory.ANALYSIS, 'pdf_metadata', 'empty_metadata',
                          targets=(FailureLocation('field', source=str(pdf_path), field_path='metadata'),))
            return None

    def _finish_extraction(
        self,
        draft: _ExtractionDraft,
        *,
        aborted: AbortedCheck,
        analysed_pages=None,
        resolved_database=None,
        prepared_chapters=None,
    ):
        if prepared_chapters is None:
            generate_chapter_files(
                pages_path=draft.pages_path,
                chapters_path=draft.chapters_path,
                toc=draft.toc,
                analysed_pages=analysed_pages,
                resolved_database=resolved_database,
            )
        elif prepared_chapters != draft.chapters_path:
            import shutil
            draft.chapters_path.mkdir(parents=True, exist_ok=True)
            for path in draft.chapters_path.glob('chapter_*.xml'):
                path.unlink()
            for path in prepared_chapters.glob('chapter_*.xml'):
                shutil.copyfile(path, draft.chapters_path / path.name)
        if draft.includes_furniture:
            write_furnitures(
                draft.pdf_path,
                draft.pages_path,
                draft.extraction_paths.furnitures,
                toc=draft.toc,
                dpi=draft.dpi if draft.dpi is not None else 300,
                aborted=aborted,
                native_pdf_text=draft.native_pdf_text,
                native_pdf_text_prepared=draft.native_pdf_text_prepared,
            )
        cover_path = draft.cover_path
        if cover_path is not None and not cover_path.exists():
            cover_path = None

        draft.extraction_paths.assets.mkdir(parents=True, exist_ok=True)
        render_dpi = draft.dpi if draft.dpi is not None else 300
        write_pages(
            draft.extraction_path,
            render_dpi=render_dpi,
            page_pixel_sizes=self._ocr.last_page_pixel_sizes,
        )
        write_manifest(
            draft.extraction_path,
            document_metadata=draft.document_metadata,
        )
        PDFCraftExtraction._from_workspace(draft.extraction_path)._validate()
        return (
            draft.extraction_paths.assets,
            draft.chapters_path,
            draft.toc_path,
            cover_path,
            draft.metering,
        )
