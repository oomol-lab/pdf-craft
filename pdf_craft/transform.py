from os import PathLike
from pathlib import Path
from typing import Callable, Literal

from epub_generator import BookMeta, LaTeXRender, TableRender

from .common import EnsureFolder, remove_surrogates
from .epub import render_epub_file
from .error import (
    IgnoreOCRErrorsChecker,
    IgnorePDFErrorsChecker,
    PDFError,
    is_inline_error,
    to_interrupted_error,
)
from .llm import LLM
from .markdown.render import render_markdown_file
from .metering import AbortedCheck, OCRTokensMetering
from .pdf import OCR, DeepSeekOCRSize, OCREvent, PDFHandler
from .sequence import generate_chapter_files
from .to_path import to_path
from .toc import analyse_toc


class Transform:
    def __init__(
        self,
        models_cache_path: PathLike | str | None = None,
        pdf_handler: PDFHandler | None = None,
        local_only: bool = False,
    ) -> None:
        self._ocr: OCR = OCR(
            model_path=models_cache_path,
            pdf_handler=pdf_handler,
            local_only=local_only,
        )

    def predownload(self, revision: str | None = None) -> None:
        self._ocr.predownload(revision)

    def load_models(self) -> None:
        self._ocr.load_models()

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
    ) -> OCRTokensMetering:  # pyright: ignore[reportReturnType]
        if markdown_assets_path is None:
            markdown_assets_path = Path(".") / "assets"
        else:
            markdown_assets_path = Path(markdown_assets_path)
        try:
            with EnsureFolder(
                path=to_path(analysing_path) if analysing_path is not None else None,
            ) as analysing_path:
                asserts_path, chapters_path, _, cover_path, metering = (
                    self._extract_from_pdf(
                        pdf_path=Path(pdf_path),
                        analysing_path=analysing_path,
                        ocr_size=ocr_size,
                        dpi=dpi,
                        max_page_image_file_size=max_page_image_file_size,
                        includes_cover=includes_cover,
                        includes_footnotes=includes_footnotes,
                        ignore_pdf_errors=ignore_pdf_errors,
                        ignore_ocr_errors=ignore_ocr_errors,
                        generate_plot=generate_plot,
                        toc_llm=toc_llm,
                        toc_assumed=toc_assumed,
                        aborted=aborted,
                        max_tokens=max_ocr_tokens,
                        max_output_tokens=max_ocr_output_tokens,
                        on_ocr_event=on_ocr_event,
                    )
                )
                render_markdown_file(
                    chapters_path=chapters_path,
                    assets_path=asserts_path,
                    output_path=Path(markdown_path),
                    output_assets_path=markdown_assets_path,
                    cover_path=cover_path,
                    aborted=aborted,
                )
                return metering

        except Exception as raw_error:
            error = to_interrupted_error(raw_error)
            if error:
                raise error from raw_error
            elif is_inline_error(raw_error):
                raise
            else:
                raise RuntimeError(
                    f"transform {pdf_path} to markdown failed"
                ) from raw_error

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
    ) -> OCRTokensMetering:  # pyright: ignore[reportReturnType]
        try:
            with EnsureFolder(
                path=to_path(analysing_path) if analysing_path is not None else None,
            ) as analysing_path:
                pdf_path = Path(pdf_path)
                asserts_path, chapters_path, toc_path, cover_path, metering = (
                    self._extract_from_pdf(
                        pdf_path=pdf_path,
                        analysing_path=analysing_path,
                        ocr_size=ocr_size,
                        dpi=dpi,
                        max_page_image_file_size=max_page_image_file_size,
                        includes_cover=includes_cover,
                        includes_footnotes=includes_footnotes,
                        ignore_pdf_errors=ignore_pdf_errors,
                        ignore_ocr_errors=ignore_ocr_errors,
                        generate_plot=generate_plot,
                        toc_llm=toc_llm,
                        toc_assumed=toc_assumed,
                        aborted=aborted,
                        max_tokens=max_ocr_tokens,
                        max_output_tokens=max_ocr_output_tokens,
                        on_ocr_event=on_ocr_event,
                    )
                )
                book_meta = book_meta or self._extract_book_meta(pdf_path)

                render_epub_file(
                    chapters_path=chapters_path,
                    toc_path=toc_path,
                    assets_path=asserts_path,
                    epub_path=Path(epub_path),
                    book_meta=book_meta,
                    lan=lan,
                    cover_path=cover_path,
                    table_render=table_render,
                    latex_render=latex_render,
                    inline_latex=inline_latex,
                    aborted=aborted,
                )
                return metering

        except Exception as raw_error:
            error = to_interrupted_error(raw_error)
            if error:
                raise error from raw_error
            elif is_inline_error(raw_error):
                raise
            else:
                raise RuntimeError(
                    f"transform {pdf_path} to epub failed"
                ) from raw_error

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
