from os import PathLike
from typing import Callable, Literal

from epub_generator import BookMeta, TableRender, LaTeXRender

from .pdf import OCR, OCREvent, DeepSeekOCRSize
from .transform import Transform
from .metering import AbortedCheck, OCRTokensMetering


def predownload_models(
        models_cache_path: PathLike | None = None,
        revision: str | None = None,
    ) -> None:
    ocr = OCR(
        model_path=models_cache_path,
        local_only=False,
    )
    ocr.predownload(revision)


def transform_markdown(
    pdf_path: PathLike | str,
    markdown_path: PathLike | str,
    markdown_assets_path: PathLike | str | None = None,
    analysing_path: PathLike | str | None = None,
    model: DeepSeekOCRSize = "gundam",
    models_cache_path: PathLike | str | None = None,
    local_only: bool = False,
    includes_footnotes: bool = False,
    ignore_fitz_errors: bool = False,
    generate_plot: bool = False,
    aborted: AbortedCheck = lambda: False,
    max_ocr_tokens: int | None = None,
    max_ocr_output_tokens: int | None = None,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> OCRTokensMetering:

    return Transform(
        models_cache_path=models_cache_path,
        local_only=local_only,
    ).transform_markdown(
        pdf_path=pdf_path,
        markdown_path=markdown_path,
        markdown_assets_path=markdown_assets_path,
        analysing_path=analysing_path,
        model=model,
        includes_footnotes=includes_footnotes,
        ignore_fitz_errors=ignore_fitz_errors,
        generate_plot=generate_plot,
        aborted=aborted,
        max_ocr_tokens=max_ocr_tokens,
        max_ocr_output_tokens=max_ocr_output_tokens,
        on_ocr_event=on_ocr_event,
    )


def transform_epub(
    pdf_path: PathLike | str,
    epub_path: PathLike | str,
    analysing_path: PathLike | str | None = None,
    model: DeepSeekOCRSize = "gundam",
    models_cache_path: PathLike | str | None = None,
    local_only: bool = False,
    includes_cover: bool = True,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    ignore_fitz_errors: bool = False,
    book_meta: BookMeta | None = None,
    lan: Literal["zh", "en"] = "zh",
    table_render: TableRender = TableRender.HTML,
    latex_render: LaTeXRender = LaTeXRender.MATHML,
    aborted: AbortedCheck = lambda: False,
    max_ocr_tokens: int | None = None,
    max_ocr_output_tokens: int | None = None,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> OCRTokensMetering:

    return Transform(
        models_cache_path=models_cache_path,
        local_only=local_only,
    ).transform_epub(
        pdf_path=pdf_path,
        epub_path=epub_path,
        analysing_path=analysing_path,
        model=model,
        includes_cover=includes_cover,
        includes_footnotes=includes_footnotes,
        generate_plot=generate_plot,
        ignore_fitz_errors=ignore_fitz_errors,
        book_meta=book_meta,
        lan=lan,
        table_render=table_render,
        latex_render=latex_render,
        aborted=aborted,
        max_ocr_tokens=max_ocr_tokens,
        max_ocr_output_tokens=max_ocr_output_tokens,
        on_ocr_event=on_ocr_event,
    )
