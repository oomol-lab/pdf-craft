import json
from pathlib import Path

from pdf_craft import LaTeXRender, OCREventKind, TableRender, TocExtractionMode, LLM, transform_epub

_IMAGE_STEM = "newton"


def main() -> None:
    project_root = Path(__file__).parent.parent
    assets_dir_path = project_root / "tests" / "assets"
    analysing_dir_path = project_root / "analysing"
    pdf_file_name = f"{_IMAGE_STEM}.pdf"

    with open(project_root / "format.json", "r", encoding="utf-8") as f:
        llm_config = json.load(f)

    toc_llm = LLM(
        key=llm_config["key"],
        url=llm_config["url"],
        model=llm_config["model"],
        token_encoding=llm_config["token_encoding"],
        timeout=llm_config["timeout"],
        retry_times=llm_config["retry_times"],
        retry_interval_seconds=llm_config["retry_interval_seconds"],
        temperature=llm_config["temperature"],
        top_p=llm_config["top_p"],
    )
    transform_epub(
        pdf_path=assets_dir_path / pdf_file_name,
        epub_path=analysing_dir_path / "output.epub",
        analysing_path=analysing_dir_path,
        models_cache_path=project_root / "models-cache",
        includes_footnotes=True,
        generate_plot=True,
        toc_mode=TocExtractionMode.LLM_ENHANCED,
        toc_llm=toc_llm,
        table_render=TableRender.HTML,
        latex_render=LaTeXRender.MATHML,
        on_ocr_event=lambda e: print(
            f"OCR {OCREventKind(e.kind).name} - Page {e.page_index}/{e.total_pages} - {_format_duration(e.cost_time_ms)}"
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
