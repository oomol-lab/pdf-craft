from os import PathLike
from pathlib import Path
from typing import Callable, Literal

from .common import EnsureFolder
from .pdf import ocr_pdf, DeepSeekOCRModel, OCREvent
from .sequence import generate_chapter_files
from .markdown import render_markdown_file
from .epub import render_epub_file
from epub_generator import TableRender, LaTeXRender


def transform_markdown(
    pdf_path: PathLike,
    markdown_path: PathLike,
    markdown_assets_path: PathLike | None = None,
    analysing_path: PathLike | None = None,
    model: DeepSeekOCRModel = "gundam",
    models_cache_path: PathLike | None = None,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> None:

    if markdown_assets_path is None:
        markdown_assets_path = Path(".") / "assets"
    else:
        markdown_assets_path = Path(markdown_assets_path)

    with EnsureFolder(analysing_path) as analysing_path:
        asserts_path = analysing_path / "assets"
        pages_path = analysing_path / "orc"
        chapters_path = analysing_path / "chapters"
        plot_path: Path | None = None
        if generate_plot:
            plot_path = analysing_path / "plots"

        ocr_pdf(
            pdf_path=Path(pdf_path),
            asset_path=asserts_path,
            ocr_path=pages_path,
            model=model,
            models_cache_path=models_cache_path,
            plot_path=plot_path,
            includes_footnotes=includes_footnotes,
            on_event=on_ocr_event,
        )
        generate_chapter_files(
            pages_path=pages_path,
            chapters_path=chapters_path,
        )
        render_markdown_file(
            chapters_path=chapters_path,
            assets_path=asserts_path,
            output_path=Path(markdown_path),
            output_assets_path=markdown_assets_path,
        )


def transform_epub(
    pdf_path: PathLike,
    epub_path: PathLike,
    analysing_path: PathLike | None = None,
    model: DeepSeekOCRModel = "gundam",
    models_cache_path: PathLike | None = None,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
    lan: Literal["zh", "en"] = "zh",
    table_render: TableRender = TableRender.HTML,
    latex_render: LaTeXRender = LaTeXRender.MATHML,
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None,
) -> None:

    with EnsureFolder(analysing_path) as analysing_path:
        asserts_path = analysing_path / "assets"
        pages_path = analysing_path / "orc"
        chapters_path = analysing_path / "chapters"
        plot_path: Path | None = None
        if generate_plot:
            plot_path = analysing_path / "plots"

        ocr_pdf(
            pdf_path=Path(pdf_path),
            asset_path=asserts_path,
            ocr_path=pages_path,
            model=model,
            models_cache_path=models_cache_path,
            plot_path=plot_path,
            includes_footnotes=includes_footnotes,
            on_event=on_ocr_event,
        )
        generate_chapter_files(
            pages_path=pages_path,
            chapters_path=chapters_path,
        )
        render_epub_file(
            chapters_path=chapters_path,
            assets_path=asserts_path,
            epub_path=Path(epub_path),
            lan=lan,
            table_render=table_render,
            latex_render=latex_render,
        )