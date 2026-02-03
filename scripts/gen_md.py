import json
from pathlib import Path

from pdf_craft import LLM, OCREventKind, transform_markdown

_IMAGE_STEM = "newton"


def main() -> None:
    project_root = Path(__file__).parent.parent
    assets_dir_path = project_root / "tests" / "assets"
    analysing_dir_path = project_root / "analysing"
    pdf_file_name = f"{_IMAGE_STEM}.pdf"
    format_json_path = project_root / "format.json"

    toc_llm: LLM | None = None
    if format_json_path.exists():
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
            log_dir_path=analysing_dir_path / "logs",
        )
    else:
        print("Warning: format.json not found, TOC LLM enhancement disabled.")

    transform_markdown(
        pdf_path=assets_dir_path / pdf_file_name,
        markdown_path=analysing_dir_path / "output.md",
        markdown_assets_path=Path("images"),
        analysing_path=analysing_dir_path,
        models_cache_path=project_root / "models-cache",
        includes_footnotes=True,
        includes_cover=True,
        generate_plot=True,
        toc_llm=toc_llm,
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
