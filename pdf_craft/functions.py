from os import PathLike
from typing import Callable, Literal

from epub_generator import BookMeta, LaTeXRender, TableRender

from .error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .pdf import OCR, DeepSeekOCRSize, OCREvent, PDFHandler
from .transform import Transform


def predownload_models(
    models_cache_path: PathLike | None = None,
    pdf_handler: PDFHandler | None = None,
    revision: str | None = None,
) -> None:
    ocr = OCR(
        model_path=models_cache_path,
        pdf_handler=pdf_handler,
        local_only=False,
    )
    ocr.predownload(revision)


def transform_markdown(
    pdf_path: PathLike | str,
    markdown_path: PathLike | str,
    pdf_handler: PDFHandler | None = None,
    markdown_assets_path: PathLike | str | None = None,
    analysing_path: PathLike | str | None = None,
    ocr_size: DeepSeekOCRSize = "gundam",
    models_cache_path: PathLike | str | None = None,
    local_only: bool = False,
    dpi: int | None = None,
    max_page_image_file_size: int | None = None,
    includes_cover: bool = False,
    includes_footnotes: bool = False,
    ignore_pdf_errors: IgnorePDFErrorsChecker = False,
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
    generate_plot: bool = False,
    toc_llm: LLM | None = None,
    toc_assumed: bool = False,
    aborted: AbortedCheck = lambda: False,
    max_ocr_tokens: int | None = None,
    max_ocr_output_tokens: int | None = None,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> OCRTokensMetering:
    return Transform(
        models_cache_path=models_cache_path,
        pdf_handler=pdf_handler,
        local_only=local_only,
    ).transform_markdown(
        pdf_path=pdf_path,
        markdown_path=markdown_path,
        markdown_assets_path=markdown_assets_path,
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
        max_ocr_tokens=max_ocr_tokens,
        max_ocr_output_tokens=max_ocr_output_tokens,
        on_ocr_event=on_ocr_event,
    )


def transform_epub(
    pdf_path: PathLike | str,
    epub_path: PathLike | str,
    pdf_handler: PDFHandler | None = None,
    analysing_path: PathLike | str | None = None,
    ocr_size: DeepSeekOCRSize = "gundam",
    models_cache_path: PathLike | str | None = None,
    local_only: bool = False,
    dpi: int | None = None,
    max_page_image_file_size: int | None = None,
    includes_cover: bool = True,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    toc_llm: LLM | None = None,
    toc_assumed: bool = False,
    ignore_pdf_errors: IgnorePDFErrorsChecker = False,
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
    book_meta: BookMeta | None = None,
    lan: Literal["zh", "en"] = "zh",
    table_render: TableRender = TableRender.HTML,
    latex_render: LaTeXRender = LaTeXRender.MATHML,
    inline_latex: bool = True,
    aborted: AbortedCheck = lambda: False,
    max_ocr_tokens: int | None = None,
    max_ocr_output_tokens: int | None = None,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> OCRTokensMetering:
    return Transform(
        models_cache_path=models_cache_path,
        pdf_handler=pdf_handler,
        local_only=local_only,
    ).transform_epub(
        pdf_path=pdf_path,
        epub_path=epub_path,
        analysing_path=analysing_path,
        ocr_size=ocr_size,
        dpi=dpi,
        max_page_image_file_size=max_page_image_file_size,
        includes_cover=includes_cover,
        includes_footnotes=includes_footnotes,
        generate_plot=generate_plot,
        toc_llm=toc_llm,
        toc_assumed=toc_assumed,
        ignore_pdf_errors=ignore_pdf_errors,
        ignore_ocr_errors=ignore_ocr_errors,
        book_meta=book_meta,
        lan=lan,
        table_render=table_render,
        latex_render=latex_render,
        inline_latex=inline_latex,
        aborted=aborted,
        max_ocr_tokens=max_ocr_tokens,
        max_ocr_output_tokens=max_ocr_output_tokens,
        on_ocr_event=on_ocr_event,
    )
