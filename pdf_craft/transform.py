# pylint: disable=protected-access

from collections.abc import Callable, Container
from os import PathLike
from pathlib import Path

from .error import (
    IgnoreOCRErrorsChecker,
    IgnorePDFErrorsChecker,
    NoUsableOCRPagesError,
    PDFError,
)
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import DeepSeekOCRSize, OCR, OCREvent, OCREventKind, PDFHandler
from .pdf.furniture import write_furnitures
from .extractor.metadata import extract_book_metadata_from_ocr, merge_ocr_and_pdf_metadata
from .extractor.chapter import generate_chapter_files
from .extractor.toc import analyse_toc
from .document import ExtractionPaths, PDFCraftExtraction, write_manifest, write_pages


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

    def _extract_from_pdf(
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
        includes_furniture: bool = False,
        extract_book_metadata: bool = False,
        metadata_llm: LLM | None = None,
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
        generate_chapter_files(pages_path=pages_path, chapters_path=chapters_path, toc=toc)
        if includes_furniture:
            write_furnitures(
                pdf_path, pages_path, extraction_paths.furnitures,
                toc=toc,
                dpi=dpi if dpi is not None else 300,
            )
        if cover_path and not cover_path.exists():
            cover_path = None

        assets_path.mkdir(parents=True, exist_ok=True)
        render_dpi = dpi if dpi is not None else 300
        write_pages(
            extraction_path,
            render_dpi=render_dpi,
            page_pixel_sizes=self._ocr.last_page_pixel_sizes,
        )
        write_manifest(extraction_path, document_metadata=document_metadata)
        PDFCraftExtraction._from_workspace(extraction_path).validate()
        return assets_path, chapters_path, toc_path, cover_path, metering

    def _extract_book_metadata(
        self,
        *,
        pdf_path: Path,
        pages_path: Path,
        enabled: bool,
        metadata_llm: LLM | None,
    ):
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
