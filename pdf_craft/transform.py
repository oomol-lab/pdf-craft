from os import PathLike
from pathlib import Path

from .common import EnsureFolder
from .pdf import ocr_pdf, DeepSeekOCRModel
from .sequence import generate_chapter_files
from .markdown import render_markdown_file


def transform_markdown(
    pdf_path: PathLike,
    markdown_path: PathLike,
    markdown_assets_path: PathLike | None = None,
    analysing_path: PathLike | None = None,
    model: DeepSeekOCRModel = "gundam",
    models_cache_path: PathLike | None = None,
    includes_footnotes: bool = False,
    generate_plot: bool = False,
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