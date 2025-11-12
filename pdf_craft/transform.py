from os import PathLike
from pathlib import Path
from typing import Callable, Literal

from epub_generator import BookMeta, TableRender, LaTeXRender

from .common import EnsureFolder
from .pdf import ocr_pdf, DeepSeekOCRModel, OCREvent
from .sequence import generate_chapter_files
from .markdown import render_markdown_file
from .epub import render_epub_file
from .aborted import AbortedCheck


def transform_markdown(
    pdf_path: PathLike,
    markdown_path: PathLike,
    markdown_assets_path: PathLike | None = None,
    analysing_path: PathLike | None = None,
    model: DeepSeekOCRModel = "gundam",
    models_cache_path: PathLike | None = None,
    local_only: bool = False,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    aborted: AbortedCheck = lambda: False,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> None:

    if markdown_assets_path is None:
        markdown_assets_path = Path(".") / "assets"
    else:
        markdown_assets_path = Path(markdown_assets_path)

    with EnsureFolder(analysing_path) as analysing_path:
        asserts_path, chapters_path, _ = _extract_data_from_pdf(
            pdf_path=pdf_path,
            analysing_path=analysing_path,
            model=model,
            models_cache_path=models_cache_path,
            local_only=local_only,
            includes_cover=False,
            includes_footnotes=includes_footnotes,
            generate_plot=generate_plot,
            aborted=aborted,
            on_ocr_event=on_ocr_event,
        )
        render_markdown_file(
            chapters_path=chapters_path,
            assets_path=asserts_path,
            output_path=Path(markdown_path),
            output_assets_path=markdown_assets_path,
            aborted=aborted,
        )

def transform_epub(
    pdf_path: PathLike,
    epub_path: PathLike,
    analysing_path: PathLike | None = None,
    model: DeepSeekOCRModel = "gundam",
    models_cache_path: PathLike | None = None,
    local_only: bool = False,
    includes_cover: bool = True,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    book_meta: BookMeta | None = None,
    lan: Literal["zh", "en"] = "zh",
    table_render: TableRender = TableRender.HTML,
    latex_render: LaTeXRender = LaTeXRender.MATHML,
    aborted: AbortedCheck = lambda: False,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> None:

    with EnsureFolder(analysing_path) as analysing_path:
        asserts_path, chapters_path, cover_path = _extract_data_from_pdf(
            pdf_path=pdf_path,
            analysing_path=analysing_path,
            model=model,
            models_cache_path=models_cache_path,
            local_only=local_only,
            includes_cover=includes_cover,
            includes_footnotes=includes_footnotes,
            generate_plot=generate_plot,
            aborted=aborted,
            on_ocr_event=on_ocr_event,
        )
        render_epub_file(
            chapters_path=chapters_path,
            assets_path=asserts_path,
            epub_path=Path(epub_path),
            book_meta=book_meta,
            lan=lan,
            cover_path=cover_path,
            table_render=table_render,
            latex_render=latex_render,
            aborted=aborted,
        )

def _extract_data_from_pdf(
    pdf_path: PathLike,
    analysing_path: Path,
    model: DeepSeekOCRModel,
    models_cache_path: PathLike | None,
    local_only: bool,
    includes_cover: bool,
    includes_footnotes: bool,
    generate_plot: bool,
    aborted: AbortedCheck,
    on_ocr_event: Callable[[OCREvent], None],
):
    asserts_path = analysing_path / "assets"
    pages_path = analysing_path / "orc"
    chapters_path = analysing_path / "chapters"

    cover_path: Path | None = None
    plot_path: Path | None = None
    if includes_cover:
        cover_path = analysing_path / "cover.png"
    if generate_plot:
        plot_path = analysing_path / "plots"

    ocr_pdf(
        pdf_path=Path(pdf_path),
        asset_path=asserts_path,
        ocr_path=pages_path,
        model=model,
        models_cache_path=models_cache_path,
        local_only=local_only,
        includes_footnotes=includes_footnotes,
        plot_path=plot_path,
        cover_path=cover_path,
        aborted=aborted,
        on_event=on_ocr_event,
    )
    generate_chapter_files(
        pages_path=pages_path,
        chapters_path=chapters_path,
    )
    return asserts_path, chapters_path, cover_path