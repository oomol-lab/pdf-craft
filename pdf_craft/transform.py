# pylint: disable=unused-argument
# pylint: disable=protected-access

from pdf_craft.pdf.backend import pdf_backend_scope, poppler_path_for
from .policies import ErrorPolicyInput
from ._policy import decision_bridge, OMITTED, policy_operation

import asyncio
from collections.abc import Callable, Container
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from os import PathLike
from pathlib import Path

from .error import (
    NoUsableOCRPagesError,
    PDFError,
)
from .refinement import Refinement
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
from .extractor.metadata import FrontPages, extract_book_metadata_from_ocr, merge_ocr_and_pdf_metadata
from .extractor.chapter import (
    generate_chapter_files,
)
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
    includes_footnotes: bool = True


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
        self._native_metadata_result: DocumentMetadata | None = None
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
        if kwargs.get("refinement") is not None and not isinstance(kwargs["refinement"], Refinement):
            raise TypeError("refinement must be Refinement or None")
        from .extractor.chapter.live import LiveExtraction
        on_metadata = kwargs.pop("on_metadata", None)
        if kwargs.get("extract_book_metadata", False) and kwargs.get("metadata_llm") is None:
            raise ValueError("extract_book_metadata=True requires metadata_llm")
        if not kwargs.get("extract_book_metadata", False):
            native = getattr(self, '_native_metadata_result', None)
            if native is not None:
                # The asynchronous handler already notified before materializing
                # page images. Do not read or settle the same work again.
                kwargs["document_metadata"] = native
            else:
                from .extractor.progress import analysis_unit
                from .progress import UnitKind
                async with analysis_unit(UnitKind.METADATA):
                    metadata = kwargs.get("document_metadata") or merge_ocr_and_pdf_metadata(
                        None, await self._read_pdf_metadata_async(kwargs["pdf_path"]))
                    if on_metadata is not None:
                        await invoke_callback(on_metadata, MetadataContent(metadata))
                    kwargs["document_metadata"] = metadata
            on_metadata = None
        context = kwargs.pop("context", None)
        on_body = kwargs.pop("on_body", None)
        on_footnote = kwargs.pop("on_footnote", None)
        async with temporary_directory("pdf-craft-live-extraction-") as directory:
            if context is not None:
                context._bind(kwargs["analysing_path"] / "extraction" / "assets")
            front = None
            if kwargs.get("extract_book_metadata", False):
                from .pdf.page_ref import pdf_pages_count
                count = await IO_DOMAIN.run(pdf_pages_count, kwargs["pdf_path"], self._ocr._get_pdf_handler())
                front = await IO_DOMAIN.run(FrontPages.open, kwargs["analysing_path"] / "ocr",
                                            count, kwargs.get("page_indexes"))
            live = LiveExtraction(directory, kwargs["analysing_path"] / "ocr",
                                  kwargs["includes_footnotes"], on_body, on_footnote,
                                  refinement=(lambda: self._stream_refinement(kwargs['refinement'], kwargs))
                                  if kwargs.get('refinement') is not None else None,
                                  page_indexes=kwargs.get("page_indexes"))
            callback = kwargs.get("on_ocr_event")
            async def event(value):
                if front is not None:
                    front.event(value)
                if context is not None:
                    context._update(self._ocr.last_page_pixel_sizes, kwargs["dpi"] or 300)
                await invoke_callback(callback, value)
                await live.event(value)
            kwargs["on_ocr_event"] = event
            try:
                if context is not None:
                    sizes = await IO_DOMAIN.run(OCR._load_page_pixel_sizes, self._ocr,
                                               kwargs["analysing_path"] / "ocr" / "page_pixel_sizes.json")
                    context._update(sizes, kwargs["dpi"] or 300)
                if front is None:
                    result = await self._extract_package_async(**kwargs, live=live, on_metadata=on_metadata)
                    return result
                from .common.tasks import joined
                async def extract_metadata():
                    from .extractor.progress import analysis_unit
                    from .progress import UnitKind
                    await front.wait_ready(3)
                    async with analysis_unit(UnitKind.METADATA):
                        value = kwargs.get("document_metadata") or await self._extract_book_metadata_async(
                            pdf_path=kwargs["pdf_path"], pages_path=kwargs["analysing_path"] / "ocr",
                            enabled=True, metadata_llm=kwargs["metadata_llm"], front_pages=front)
                        if on_metadata is not None:
                            await invoke_callback(on_metadata, MetadataContent(value or DocumentMetadata()))
                        return value
                metadata_task = asyncio.create_task(extract_metadata())
                async def extract():
                    return await self._extract_package_async(**kwargs, live=live,
                                                             metadata_task=metadata_task, front_pages=front)
                async def observe_metadata():
                    return await metadata_task
                try:
                    result, _ = await joined(extract(), observe_metadata())
                    return result
                finally:
                    from .concurrency import settle_tasks
                    metadata_task.cancel()
                    await settle_tasks([metadata_task])
            finally:
                await live.cancel()
                if context is not None:
                    context._unbind()

    async def _extract_package_async(self, **kwargs):
        """Run optional footnote refinement between two OCR-domain phases."""

        live = kwargs.pop("live", None)
        metadata_task = kwargs.pop("metadata_task", None)
        front_pages = kwargs.pop("front_pages", None)
        on_metadata = kwargs.pop("on_metadata", None)
        refinement = kwargs.pop("refinement", None)
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
        if refinement is not None and not isinstance(refinement, Refinement):
            raise TypeError("refinement must be Refinement")
        draft = await run_cancellable(
            OCR_DOMAIN,
            lambda aborted: self._prepare_extraction(**{**kwargs, "aborted": aborted}),
            original_aborted=original_aborted,
        )
        if front_pages is not None:
            front_pages.finish()
        if live is not None:
            await live.finish()
        from .extractor.progress import active_extraction
        progress = active_extraction()
        if progress is not None:
            await progress.cached_pages()
        if metadata_task is not None:
            draft = replace(draft, document_metadata=await metadata_task)
        draft = await self._resolve_llm_stages(draft, kwargs, on_metadata, metadata_resolved=metadata_task is not None)
        try:
            if live is not None and live.analysis_error is not None:
                raise live.analysis_error
            return await self._commit_extraction(
                draft, analysed_pages=None,
                resolved_database=(live.database if live is not None and
                                   (refinement is not None or not draft.toc.page_indexes) else None),
                aborted=original_aborted, toc_llm=kwargs["toc_llm"], toc_assumed=kwargs["toc_assumed"],
            )
        except Exception as error:
            from .extractor.recovery import recover_chapters, recoverable_structure
            if not recoverable_structure(error):
                raise
            async with temporary_directory("pdf-craft-page-recovery-") as directory:
                recovered = await recover_chapters(
                    draft, self._ocr, error, directory,
                    analyses=(),
                    adopted_database=(live.database if refinement is not None and live is not None else None),
                    aborted=original_aborted or (lambda: False),
                )
                draft = replace(draft, toc=recovered.toc)
                return await self._commit_extraction(
                    draft, analysed_pages=None, resolved_database=None,
                    prepared_chapters=recovered.chapters, aborted=original_aborted,
                    toc_llm=kwargs["toc_llm"], toc_assumed=kwargs["toc_assumed"])

    async def _commit_extraction(self, draft, *, analysed_pages, resolved_database, aborted,
                                      refinement_usage=None, prepared_chapters=None,
                                      toc_llm=None, toc_assumed=False):
        """Commit one adopted flow before deriving any final semantic consumers."""
        from .common import save_xml
        from .extractor.commit import project_adopted, partition_adopted
        from .extractor.progress import analysis_unit
        from .progress import UnitKind
        from .extractor.toc import encode as encode_toc
        async with temporary_directory("pdf-craft-adopted-") as directory:
            adopted = directory / "chapters"
            if prepared_chapters is None:
                await run_cancellable(OCR_DOMAIN, lambda _check: generate_chapter_files(
                    pages_path=draft.pages_path, chapters_path=adopted,
                    toc=TocInfo([], draft.toc.page_indexes), analysed_pages=analysed_pages,
                    resolved_database=resolved_database, chunk_size=128,
                    includes_footnotes=draft.includes_footnotes), original_aborted=aborted)
            else:
                adopted = prepared_chapters
            projection = directory / "pages"
            await IO_DOMAIN.run(project_adopted, adopted, draft.pages_path, projection, draft.toc.page_indexes)
            async with analysis_unit(UnitKind.TOC):
                toc = await analyse_toc(
                    pages_path=projection, toc_path=directory / 'toc.xml',
                    toc_llm=toc_llm, toc_assumed=toc_assumed,
                    printed_page_indexes=draft.toc.page_indexes)
            draft = replace(draft, toc=toc)
            await IO_DOMAIN.run(partition_adopted, adopted, draft.chapters_path, toc)
            await IO_DOMAIN.run(save_xml, encode_toc(toc), draft.toc_path)
            result = await run_cancellable(OCR_DOMAIN, lambda check: self._finish_extraction(
                draft, aborted=check, prepared_chapters=draft.chapters_path,
                furniture_pages_path=projection), original_aborted=aborted)
            if refinement_usage is not None:
                refinement_usage.accept()
            return result

    @asynccontextmanager
    async def _stream_refinement(self, options, kwargs):
        from .extractor.chapter.page_review import build_jev_review_requests
        includes_footnotes = kwargs['includes_footnotes']
        from .extractor.chapter.page_repair import repair_page_with_llm
        from .extractor.progress import page_stage
        from .common import read_xml
        from .pdf import decode
        from ._policy import recover, locations
        from .policies import FailureCategory, FailureLocation
        from .usage import UsageScope
        from .extractor.refinement import PageResult
        runtime = runtime_for(options.llm, options.resolved_llm_executor(),
                              protocol_version='footnote-refinement-json-v2')
        async def request(messages, index, maximum):
            return await runtime.request(messages, max_tokens=options.max_output_tokens,
                                         retry_index=index, retry_max=maximum)
        async with JEVRuntime(options.jev, options.resolved_jev_executor()) as jev:
            async def process(previous, target, following):
                neighbors = [page for page in (previous, target, following) if page is not None]
                source = await IO_DOMAIN.run(lambda: [decode(read_xml(
                    kwargs['analysing_path'] / 'ocr' / f'page_{page.page_index}.xml'))
                    for page in neighbors])
                packet = next(packet for index, packet in build_jev_review_requests(
                    source, neighbors, self._ocr.last_page_pixel_sizes, includes_footnotes=includes_footnotes) if index == target.page_index)
                location = FailureLocation('page', page_indexes=(target.page_index,))
                context = tuple(FailureLocation('page', page_indexes=(page.page_index,))
                                for page in (previous, following) if page is not None)
                with locations((location,)):
                    await page_stage(target.page_index, 5)
                    try:
                        probability = await jev.evaluate(target.page_index, packet)
                        if not 0 <= probability <= 1:
                            raise ValueError('JEV pass probability must be between 0 and 1')
                    except Exception as error:
                        await recover(error, FailureCategory.ANALYSIS, 'jev_review', 'algorithm_result',
                                      targets=(location,), context_locations=context)
                        return PageResult(target)
                    if round(1 - probability, 12) < options.risk_threshold:
                        return PageResult(target)
                    await page_stage(target.page_index, 6)
                    with UsageScope(provider=options.llm) as usage:
                        try:
                            result = await repair_page_with_llm(
                                previous_page=previous, target_page=target, next_page=following,
                                previous_pass_probability=None, next_pass_probability=None,
                                target_pass_probability=probability, request=request,
                                includes_footnotes=includes_footnotes)
                            if any(layout.annotations.preserve_as_image for layout in result.layouts):
                                from .extractor.page_graphics import preserve_graphics
                                result = await run_cancellable(OCR_DOMAIN, lambda check: preserve_graphics(
                                    result, kwargs['pdf_path'], self._ocr._get_pdf_handler(),
                                    self._ocr.last_page_pixel_sizes[target.page_index],
                                    kwargs['analysing_path']/'extraction'/'assets', kwargs['dpi'] or 300, check),
                                    original_aborted=kwargs.get('aborted'))
                        except Exception as error:
                            await recover(error, FailureCategory.ANALYSIS, 'footnote_repair', 'algorithm_result',
                                          targets=(location,), context_locations=context)
                            return PageResult(target)
                        return PageResult(result, usage.defer(), target)
            yield process

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
            includes_footnotes=includes_footnotes,
            native_pdf_text=native_pdf_text,
            native_pdf_text_prepared=native_pdf_text_prepared,
        )

    async def _resolve_llm_stages(self, draft: _ExtractionDraft, kwargs: dict, on_metadata=None, *, metadata_resolved=False) -> _ExtractionDraft:
        from .extractor.progress import analysis_unit
        from .progress import UnitKind
        if kwargs['extract_book_metadata'] and not metadata_resolved:
            async with analysis_unit(UnitKind.METADATA):
                metadata = draft.document_metadata or await self._extract_book_metadata_async(
                    pdf_path=draft.pdf_path, pages_path=draft.pages_path,
                    enabled=True, metadata_llm=kwargs['metadata_llm'])
        else:
            metadata = draft.document_metadata
        if on_metadata is not None:
            await invoke_callback(on_metadata, MetadataContent(metadata or DocumentMetadata()))
        from .extractor.toc.analysing import detect_printed_pages
        printed = (await IO_DOMAIN.run(detect_printed_pages, draft.pages_path)
                   if kwargs['toc_assumed'] else [])
        toc = TocInfo([], [page.page_index for page in printed])
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
        front_pages: FrontPages | None = None,
    ) -> DocumentMetadata | None:
        if not enabled:
            return None
        pdf_metadata = await self._read_pdf_metadata_async(pdf_path)
        ocr_metadata = None
        try:
            assert metadata_llm is not None
            ocr_metadata = await extract_book_metadata_from_ocr(pages_path, metadata_llm, **({"front_pages": front_pages} if front_pages is not None else {}))
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
        furniture_pages_path=None,
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
                furniture_pages_path or draft.pages_path,
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
