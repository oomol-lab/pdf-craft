from os import PathLike
from pathlib import Path
from collections.abc import Sequence
from typing import Callable, Container, Literal, NoReturn

from epub_generator import BookMeta, LaTeXRender, TableRender

from .common import EnsureFolder, remove_surrogates
from .error import (
    IgnoreOCRErrorsChecker,
    IgnorePDFErrorsChecker,
    InterruptedError as PDFInterruptedError,
    PDFError,
    is_inline_error,
    to_interrupted_error,
)
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import OCR, DeepSeekOCRSize, OCREvent, PDFHandler
from .sequence import generate_chapter_files
from .to_path import to_path
from .toc import analyse_toc
from .document import DocumentPackage
from .craft import TranslationStep
from .transformer import PackageTransformer


class Transform:
    def __init__(
        self,
        models_cache_path: PathLike | str | None = None,
        pdf_handler: PDFHandler | None = None,
        local_only: bool = False,
        ocr: OCRConfig | None = None,
    ) -> None:
        self._ocr: OCR = OCR(
            ocr=ensure_ocr_config(ocr, models_cache_path, local_only),
            pdf_handler=pdf_handler,
        )

    def predownload(self, revision: str | None = None) -> None:
        self._ocr.predownload(revision)

    def load_models(self) -> None:
        self._ocr.load_models()

    def extract_package(self, **kwargs):
        """Compatibility extraction hook used by the public PDFExtractor."""
        return self._extract_from_pdf(**kwargs)

    def transform_markdown(
        self,
        pdf_path: PathLike | str,
        markdown_path: PathLike | str,
        markdown_assets_path: PathLike | str | None = None,
        analysing_path: PathLike | str | None = None,
        ocr_size: DeepSeekOCRSize = "gundam",
        dpi: int | None = None,
        max_page_image_file_size: int | None = None,
        includes_cover: bool = False,
        includes_footnotes: bool = False,
        generate_plot: bool = False,
        toc_assumed: bool = False,
        toc_llm: LLM | None = None,
        ignore_pdf_errors: IgnorePDFErrorsChecker = False,
        ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
        aborted: AbortedCheck = lambda: False,
        max_ocr_tokens: int | None = None,
        max_ocr_output_tokens: int | None = None,
        on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
        steps: Sequence[TranslationStep | PackageTransformer] = (),
    ) -> OCRTokensMetering:  # pyright: ignore[reportReturnType]
        # Compatibility wrapper.  PDFCraft owns the production workflow.
        from .craft import ExtractionOptions, PDFCraft
        if markdown_assets_path is None:
            markdown_assets_path = Path(markdown_path).parent / "assets"
        try:
            with EnsureFolder(path=to_path(analysing_path) if analysing_path is not None else None) as package_path:
                return PDFCraft.from_engine(self).convert_pdf_to_markdown(
                    pdf_path, markdown_path, package_path=package_path,
                    assets_path=markdown_assets_path,
                    extraction=ExtractionOptions(
                        ocr_size=ocr_size, dpi=dpi,
                        max_page_image_file_size=max_page_image_file_size,
                        includes_cover=includes_cover, includes_footnotes=includes_footnotes,
                        generate_plot=generate_plot, toc_assumed=toc_assumed, toc_llm=toc_llm,
                        ignore_pdf_errors=ignore_pdf_errors, ignore_ocr_errors=ignore_ocr_errors,
                        aborted=aborted, max_ocr_tokens=max_ocr_tokens,
                        max_ocr_output_tokens=max_ocr_output_tokens, on_ocr_event=on_ocr_event,
                    ),
                    steps=steps,
                )
        except Exception as error:
            self._raise_compatibility_error(error, pdf_path, "markdown")

    def transform_epub(
        self,
        pdf_path: PathLike | str,
        epub_path: PathLike | str,
        analysing_path: PathLike | str | None = None,
        ocr_size: DeepSeekOCRSize = "gundam",
        dpi: int | None = None,
        max_page_image_file_size: int | None = None,
        includes_cover: bool = True,
        includes_footnotes: bool = False,
        ignore_pdf_errors: IgnorePDFErrorsChecker = False,
        ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
        generate_plot: bool = False,
        toc_assumed: bool = True,
        toc_llm: LLM | None = None,
        book_meta: BookMeta | None = None,
        lan: Literal["zh", "en"] = "zh",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
        max_ocr_tokens: int | None = None,
        max_ocr_output_tokens: int | None = None,
        on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
        steps: Sequence[TranslationStep | PackageTransformer] = (),
    ) -> OCRTokensMetering:  # pyright: ignore[reportReturnType]
        from .craft import ExtractionOptions, PDFCraft
        try:
            with EnsureFolder(path=to_path(analysing_path) if analysing_path is not None else None) as package_path:
                return PDFCraft.from_engine(self).convert_pdf_to_epub(
                    pdf_path, epub_path, package_path=package_path,
                    book_meta=book_meta, lan=lan, table_render=table_render,
                    latex_render=latex_render, inline_latex=inline_latex,
                    extraction=ExtractionOptions(
                        ocr_size=ocr_size, dpi=dpi,
                        max_page_image_file_size=max_page_image_file_size,
                        includes_cover=includes_cover, includes_footnotes=includes_footnotes,
                        generate_plot=generate_plot, toc_assumed=toc_assumed, toc_llm=toc_llm,
                        ignore_pdf_errors=ignore_pdf_errors, ignore_ocr_errors=ignore_ocr_errors,
                        aborted=aborted, max_ocr_tokens=max_ocr_tokens,
                        max_ocr_output_tokens=max_ocr_output_tokens, on_ocr_event=on_ocr_event,
                    ),
                    steps=steps,
                )
        except Exception as error:
            self._raise_compatibility_error(error, pdf_path, "epub")

    def _raise_compatibility_error(
        self, error: Exception, source: PathLike | str, target: str,
    ) -> NoReturn:
        if isinstance(error, PDFInterruptedError):
            raise error
        interrupted = to_interrupted_error(error)
        if interrupted is not None:
            raise interrupted from error
        if is_inline_error(error):
            raise error
        raise RuntimeError(f"transform {source} to {target} failed") from error

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
    ):
        asserts_path = analysing_path / "assets"
        pages_path = analysing_path / "ocr"
        chapters_path = analysing_path / "chapters"
        toc_path = analysing_path / "toc.xml"

        cover_path: Path | None = None
        plot_path: Path | None = None
        if includes_cover:
            cover_path = analysing_path / "cover.png"
        if generate_plot:
            plot_path = analysing_path / "plots"

        metering = OCRTokensMetering(
            input_tokens=0,
            output_tokens=0,
        )
        existing_page_pixel_sizes = DocumentPackage.from_path(analysing_path).page_pixel_sizes()
        for event in self._ocr.recognize(
            pdf_path=pdf_path,
            asset_path=asserts_path,
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

        toc = analyse_toc(
            pages_path=pages_path,
            toc_path=toc_path,
            toc_llm=toc_llm,
            toc_assumed=toc_assumed,
        )
        generate_chapter_files(
            pages_path=pages_path,
            chapters_path=chapters_path,
            toc=toc,
        )
        if cover_path and not cover_path.exists():
            cover_path = None

        page_pixel_sizes = existing_page_pixel_sizes | self._ocr.last_page_pixel_sizes
        DocumentPackage(chapters_path, asserts_path, toc_path, cover_path).write_metadata(
            dpi=dpi if dpi is not None else 300, page_pixel_sizes=page_pixel_sizes
        )

        return asserts_path, chapters_path, toc_path, cover_path, metering

    def _extract_book_meta(self, pdf_path: Path) -> BookMeta | None:
        try:
            pdf_metadata = self._ocr.metadata(pdf_path)
            return BookMeta(
                title=self._normalize_text_in_meta(pdf_metadata.title) or pdf_path.stem,
                description=self._normalize_text_in_meta(pdf_metadata.description),
                publisher=self._normalize_text_in_meta(pdf_metadata.publisher),
                isbn=self._normalize_text_in_meta(pdf_metadata.isbn),
                authors=[remove_surrogates(s) for s in pdf_metadata.authors],
                editors=[remove_surrogates(s) for s in pdf_metadata.editors],
                translators=[remove_surrogates(s) for s in pdf_metadata.translators],
                modified=pdf_metadata.modified,
            )
        except PDFError:
            print("Warning: Failed to extract PDF metadata.")
            return None

    def _normalize_text_in_meta(self, text: str | None) -> str | None:
        if text is None:
            return None
        return remove_surrogates(text)
