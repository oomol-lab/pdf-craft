# pylint: disable=protected-access

from collections.abc import Callable, Container
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from .error import (
    IgnoreOCRErrorsChecker,
    IgnorePDFErrorsChecker,
    NoUsableOCRPagesError,
    PDFError,
)
from .footnote import FootnoteRefinement
from .jev import JEVRuntime
from .llm import LLM, runtime_for
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import DeepSeekOCRSize, OCR, OCREvent, OCREventKind, PDFHandler
from .pdf.furniture import write_furnitures
from .runtime import OCR_DOMAIN, run_cancellable, run_subprocess
from .extractor.metadata import extract_book_metadata_from_ocr, merge_ocr_and_pdf_metadata
from .extractor.chapter import (
    ChapterAnalysis,
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
    ) -> None:
        self._ocr = OCR(
            ocr=ensure_ocr_config(ocr, models_cache_path, local_only),
            pdf_handler=pdf_handler,
        )

    def predownload(self, revision: str | None = None) -> None:
        self._ocr.predownload(revision)

    def load_models(self) -> None:
        self._ocr.load_models()

    def extract_package(self, **kwargs):
        """Extraction hook used by :class:`~pdf_craft.extractor.PDFExtractor`."""
        return self._extract_from_pdf(**kwargs)

    async def extract_package_async(self, **kwargs):
        """Run optional footnote refinement between two OCR-domain phases."""

        footnote_refinement = kwargs.pop("footnote_refinement", None)
        original_aborted = kwargs.get("aborted")
        if footnote_refinement is None:
            return await run_cancellable(
                OCR_DOMAIN,
                lambda aborted: self._extract_from_pdf(
                    **{**kwargs, "aborted": aborted}
                ),
                original_aborted=original_aborted,
            )
        if not isinstance(footnote_refinement, FootnoteRefinement):
            raise TypeError("footnote_refinement must be FootnoteRefinement")

        draft, chapter_analysis = await run_cancellable(
            OCR_DOMAIN,
            lambda aborted: self._prepare_repair(
                **{**kwargs, "aborted": aborted}
            ),
            original_aborted=original_aborted,
        )
        runtime = runtime_for(
            footnote_refinement.llm,
            protocol_version="footnote-refinement-json-v1",
        )

        async def request(messages, index, maximum):
            return await runtime.request(
                messages,
                max_tokens=footnote_refinement.max_output_tokens,
                retry_index=index,
                retry_max=maximum,
            )

        async with JEVRuntime(footnote_refinement.jev) as jev:
            processor = JevLlmRepairProcessor(
                jev.evaluate,
                request,
                threshold=footnote_refinement.risk_threshold,
                max_retries=footnote_refinement.max_retries,
                concurrency=footnote_refinement.jev.concurrency,
            )
            repaired_pages = await processor(
                chapter_analysis.source_pages,
                chapter_analysis.pages,
                chapter_analysis.page_pixel_sizes,
            )

        return await run_cancellable(
            OCR_DOMAIN,
            lambda aborted: self._finish_extraction(
                draft,
                aborted=aborted,
                analysed_pages=repaired_pages,
            ),
            original_aborted=original_aborted,
        )

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
        try:
            stdout, _ = await run_subprocess(
                "pdftotext", "-bbox-layout", str(pdf_path), "-",
            )
        except (FileNotFoundError, RuntimeError):
            stdout = None
        return {
            "native_pdf_text": stdout,
            "native_pdf_text_prepared": True,
        }

    def _extract_from_pdf(self, **kwargs):
        draft = self._prepare_extraction(**kwargs)
        return self._finish_extraction(draft, aborted=kwargs["aborted"])

    def _prepare_repair(self, **kwargs) -> tuple[_ExtractionDraft, ChapterAnalysis]:
        draft = self._prepare_extraction(**kwargs)
        return draft, prepare_chapter_analysis(draft.pages_path, draft.toc)

    def _prepare_extraction(
        self,
        pdf_path: Path,
        analysing_path: Path,
        ocr_size: DeepSeekOCRSize,
        dpi: int | None,
        max_page_image_file_size: int | None,
        includes_cover: bool,
        includes_footnotes: bool,
        ignore_pdf_errors: IgnorePDFErrorsChecker,
        ignore_ocr_errors: IgnoreOCRErrorsChecker,
        generate_plot: bool,
        toc_llm: LLM | None,
        toc_assumed: bool,
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
        metering = OCRTokensMetering(input_tokens=0, output_tokens=0)
        usable_pages = 0
        failed_page_indexes: list[int] = []
        for event in self._ocr.recognize(
            pdf_path=pdf_path,
            asset_path=assets_path,
            ocr_path=pages_path,
            ocr_size=ocr_size,
            dpi=dpi,
            max_page_image_file_size=max_page_image_file_size,
            includes_footnotes=includes_footnotes,
            ignore_pdf_errors=ignore_pdf_errors,
            ignore_ocr_errors=ignore_ocr_errors,
            plot_path=plot_path,
            cover_path=cover_path,
            aborted=aborted,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
            page_indexes=page_indexes if page_indexes is not None else range(1, 2**31),
        ):
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
        document_metadata = self._extract_book_metadata(
            pdf_path=pdf_path,
            pages_path=pages_path,
            enabled=extract_book_metadata,
            metadata_llm=metadata_llm,
        )
        toc = analyse_toc(
            pages_path=pages_path,
            toc_path=toc_path,
            toc_llm=toc_llm,
            toc_assumed=toc_assumed,
        )
        return _ExtractionDraft(
            pdf_path=pdf_path,
            extraction_path=extraction_path,
            extraction_paths=extraction_paths,
            pages_path=pages_path,
            chapters_path=chapters_path,
            toc_path=toc_path,
            toc=toc,
            cover_path=cover_path,
            document_metadata=document_metadata,
            metering=metering,
            dpi=dpi,
            includes_furniture=includes_furniture,
            native_pdf_text=native_pdf_text,
            native_pdf_text_prepared=native_pdf_text_prepared,
        )

    def _finish_extraction(
        self,
        draft: _ExtractionDraft,
        *,
        aborted: AbortedCheck,
        analysed_pages=None,
    ):
        generate_chapter_files(
            pages_path=draft.pages_path,
            chapters_path=draft.chapters_path,
            toc=draft.toc,
            analysed_pages=analysed_pages,
        )
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

    def _extract_book_metadata(
        self,
        *,
        pdf_path: Path,
        pages_path: Path,
        enabled: bool,
        metadata_llm: LLM | None,
    ) -> DocumentMetadata | None:
        if not enabled:
            return None
        ocr_metadata = None
        try:
            assert metadata_llm is not None
            ocr_metadata = extract_book_metadata_from_ocr(pages_path, metadata_llm)
        except Exception as error:  # Metadata is optional; extraction must remain usable.
            print(f"Warning: Failed to extract book metadata from OCR: {error}")
        pdf_metadata = None
        try:
            pdf_metadata = self._ocr.metadata(pdf_path)
        except PDFError:
            print("Warning: Failed to read PDF file metadata for book metadata fallback.")
        return merge_ocr_and_pdf_metadata(ocr_metadata, pdf_metadata)
