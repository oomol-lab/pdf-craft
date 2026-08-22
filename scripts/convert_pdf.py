#!/usr/bin/env python3
"""Manually extract a PDF package and optionally render it with pdf-craft 2.0."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from pdf_craft import ExtractionOptions, PDFCraft, PDFOptions

if __package__:
    from .runtime import create_ocr_config_from_env, load_project_env
else:
    from runtime import create_ocr_config_from_env, load_project_env


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parent.parent
    load_project_env(project_root)
    output_root = _output_root(args.output_root, args.source)
    package_path = output_root / "package"
    craft = PDFCraft(pdf=PDFOptions(ocr=create_ocr_config_from_env()))
    package, metering = craft.extract_pdf_with_metering(
        args.source,
        package_path,
        ExtractionOptions(
            page_indexes=_page_indexes(args.pages),
            includes_cover=args.cover,
            includes_footnotes=args.footnotes,
            generate_plot=args.plot,
            toc_assumed=args.toc_assumed,
            on_ocr_event=_print_ocr_event,
        ),
    )
    if args.format == "markdown":
        craft.render_markdown(package, output_root / "book.md", Path("assets"))
    elif args.format == "epub":
        craft.render_epub(package, output_root / "book.epub")
    print(f"Package: {package_path}")
    print(f"Output: {output_root}")
    print(f"OCR tokens: input={metering.input_tokens}, output={metering.output_tokens}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source PDF")
    parser.add_argument("--format", choices=("package", "markdown", "epub"), default="markdown")
    parser.add_argument("--pages", help="comma-separated 1-based page indexes, such as 1,2,3")
    parser.add_argument("--output-root", type=Path, help="directory for this conversion run")
    parser.add_argument("--cover", action="store_true", help="extract a cover image")
    parser.add_argument("--footnotes", action="store_true", help="include footnote layouts")
    parser.add_argument("--plot", action="store_true", help="write OCR plot diagnostics")
    parser.add_argument("--toc-assumed", action="store_true", help="treat detected TOC pages as a TOC")
    return parser.parse_args()


def _output_root(value: Path | None, source: Path) -> Path:
    if value is not None:
        value.mkdir(parents=True, exist_ok=False)
        return value
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path("analysing") / "manual" / f"{stamp}-{source.stem}"
    root.mkdir(parents=True, exist_ok=False)
    return root


def _page_indexes(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    try:
        pages = tuple(int(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise SystemExit("--pages must be comma-separated positive integers") from error
    if not pages or any(page < 1 for page in pages):
        raise SystemExit("--pages uses 1-based positive page indexes")
    return pages


def _print_ocr_event(event) -> None:
    print(f"OCR {event.kind.name.lower()}: page {event.page_index}/{event.total_pages}")


if __name__ == "__main__":
    main()
