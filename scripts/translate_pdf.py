#!/usr/bin/env python3
"""Extract, translate, and patch a PDF with the pdf-craft 2.0 public API."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pdf_craft import ExtractionOptions, PDFCraft, PDFOptions, XMLTranslator
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer

if __package__:
    from .convert_pdf import _page_indexes, _print_ocr_event
    from .runtime import create_ocr_config_from_env, create_translation_llm_from_env, load_project_env
else:
    from convert_pdf import _page_indexes, _print_ocr_event
    from runtime import create_ocr_config_from_env, create_translation_llm_from_env, load_project_env


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parent.parent
    load_project_env(project_root)
    output_root = _output_root(args.output_root, args.source)
    llm = create_translation_llm_from_env(
        cache_path=output_root / "translation-cache",
        log_dir_path=output_root / "translation-logs",
    )
    craft = PDFCraft(pdf=PDFOptions(ocr=create_ocr_config_from_env()))
    package, metering = craft.extract_pdf_with_metering(
        args.source,
        output_root / "package",
        ExtractionOptions(
            page_indexes=_page_indexes(args.pages),
            includes_cover=False,
            includes_footnotes=args.footnotes,
            on_ocr_event=_print_ocr_event,
        ),
    )
    translator = XMLTranslator(
        translation_llm=llm,
        fill_llm=llm,
        target_language=args.target_language,
        user_prompt=args.prompt,
        ignore_translated_error=False,
        max_retries=args.max_retries,
        max_fill_displaying_errors=3,
        max_group_score=args.max_group_tokens,
        cache_seed_content=f"manual-pdf-translation:{args.target_language}",
    )
    output = output_root / f"{args.source.stem}-{args.target_language}.pdf"
    craft.translate_pdf(args.source, package, output, ChapterXMLTransformer(cast(Any, translator)))
    print(f"Package: {output_root / 'package'}")
    print(f"Translated PDF: {output}")
    print(f"OCR tokens: input={metering.input_tokens}, output={metering.output_tokens}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source PDF")
    parser.add_argument("target_language", help="translation target language")
    parser.add_argument("--pages", help="comma-separated 1-based page indexes, such as 1,2,3")
    parser.add_argument("--output-root", type=Path, help="directory for this translation run")
    parser.add_argument("--prompt", help="additional translation instruction")
    parser.add_argument("--footnotes", action="store_true", help="include footnote layouts")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-group-tokens", type=int, default=2600)
    return parser.parse_args()


def _output_root(value: Path | None, source: Path) -> Path:
    if value is not None:
        value.mkdir(parents=True, exist_ok=False)
        return value
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path("analysing") / "manual" / f"{stamp}-{source.stem}-translation"
    root.mkdir(parents=True, exist_ok=False)
    return root


if __name__ == "__main__":
    main()
