from pathlib import Path
from pdf_craft import transform_epub, OCREventKind, TableRender, LaTeXRender, BookMeta


_IMAGE_STEM = "newton"

def main() -> None:
    project_root = Path(__file__).parent.parent
    assets_dir_path = project_root / "tests" / "assets"
    analysing_dir_path = project_root / "analysing"
    pdf_file_name = f"{_IMAGE_STEM}.pdf"

    transform_epub(
        pdf_path=assets_dir_path / pdf_file_name,
        epub_path=analysing_dir_path / "output.epub",
        analysing_path=analysing_dir_path,
        models_cache_path=project_root / "models-cache",
        # includes_footnotes=True,
        generate_plot=True,
        table_render=TableRender.HTML,
        latex_render=LaTeXRender.MATHML,
        on_ocr_event=lambda e: print(f"OCR {OCREventKind(e.kind).name} - Page {e.page_index}/{e.total_pages} - {_format_duration(e.cost_time_ms)}"),
        book_meta=BookMeta(
            title="Sample Book",
            authors=["Tao Zeyu"],
        ),
    )

def _format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    elif ms < 60000:
        seconds = ms / 1000
        return f"{seconds:.2f}s"
    else:
        minutes = ms // 60000
        seconds = (ms % 60000) / 1000
        return f"{minutes}m {seconds:.2f}s"

if __name__ == "__main__":
    main()
