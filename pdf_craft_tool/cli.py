"""Repository-local CLI for repeatable pdf-craft conversions and smoke runs."""

import argparse
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from pdf_craft import (
    ChapterPackageTransformer,
    ChapterXMLTransformer,
    DocumentPackage,
    ExtractionOptions,
    OCRMode,
    OCRTokensMetering,
    PDFCraft,
    PDFOptions,
    SubmitKind,
    TranslationStep,
    XMLTranslator,
)

from .runtime import (
    create_ocr_config_from_env,
    create_llm_from_env,
    load_project_env,
    ocr_mode_from_env,
    ocr_values_from_env,
    llm_values_from_env,
)
from .paths import DEFAULT_OUTPUT_ROOT, create_run_directory
from .smoke import SmokeRun, expand_matrix, run_smoke
from .smoke.assets import discover_assets


@dataclass(frozen=True)
class _ExtractionResult:
    craft: PDFCraft
    package: DocumentPackage
    metering: OCRTokensMetering


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    result = args.handler(args)
    return result if isinstance(result, int) else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", title="commands")

    pdf = commands.add_parser("pdf", help="extract, convert, or translate a PDF")
    pdf_commands = pdf.add_subparsers(dest="pdf_command", required=True)
    extract = pdf_commands.add_parser("extract", help="PDF -> DocumentPackage")
    _add_pdf_source(extract)
    _add_extraction_options(extract)
    extract.set_defaults(handler=_extract_pdf)

    convert = pdf_commands.add_parser("convert", help="PDF -> Markdown or EPUB")
    _add_pdf_source(convert)
    convert.add_argument("--format", choices=("markdown", "epub"), required=True)
    convert.add_argument("--output", type=Path, help="rendered file; defaults inside --work-dir")
    _add_extraction_options(convert)
    convert.set_defaults(handler=_convert_pdf)

    translate = pdf_commands.add_parser("translate", help="PDF -> translated Markdown, EPUB, or PDF")
    _add_pdf_source(translate)
    translate.add_argument("target_language")
    translate.add_argument("--format", choices=("markdown", "epub", "pdf"), default="pdf")
    translate.add_argument("--output", type=Path, help="translated file; defaults inside --work-dir")
    _add_translation_options(translate)
    _add_extraction_options(translate)
    translate.set_defaults(handler=_translate_pdf)

    package = commands.add_parser("package", help="operate on an existing DocumentPackage")
    package_commands = package.add_subparsers(dest="package_command", required=True)
    render = package_commands.add_parser("render", help="DocumentPackage -> Markdown or EPUB")
    render.add_argument("package", type=Path)
    render.add_argument("--format", choices=("markdown", "epub"), required=True)
    render.add_argument("--output", type=Path, help="rendered file; defaults inside --work-dir")
    _add_work_dir(render, "isolated run directory")
    render.set_defaults(handler=_render_package)

    epub = commands.add_parser("epub", help="translate an existing EPUB")
    epub_commands = epub.add_subparsers(dest="epub_command", required=True)
    epub_translate = epub_commands.add_parser("translate", help="EPUB -> translated EPUB")
    epub_translate.add_argument("source", type=Path)
    epub_translate.add_argument("target_language")
    epub_translate.add_argument("--output", type=Path, help="translated file; defaults inside --work-dir")
    _add_work_dir(epub_translate, "isolated run directory")
    _add_translation_options(epub_translate)
    epub_translate.set_defaults(handler=_translate_epub)

    smoke = commands.add_parser("smoke", help="run parameterized smoke conversions and reports")
    smoke_commands = smoke.add_subparsers(dest="smoke_command", required=True)
    assets = smoke_commands.add_parser("assets", help="list discovered PDF and EPUB assets")
    assets.add_argument("--assets-root", type=Path, default=Path("tests/assets"))
    assets.set_defaults(handler=_list_assets)

    run = smoke_commands.add_parser("run", help="run one matrix route from command-line arguments")
    run.add_argument("--asset", required=True, help="path relative to --assets-root")
    run.add_argument(
        "--route",
        choices=(
            "package", "package-markdown", "package-epub", "markdown", "epub",
            "pdf-patch", "epub-check", "epub-translate",
        ),
        required=True,
    )
    run.add_argument("--assets-root", type=Path, default=Path("tests/assets"))
    run.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "smoke")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--ocr-mode", choices=_ocr_modes())
    _add_smoke_options(run)
    run.set_defaults(handler=_run_smoke)

    matrix = smoke_commands.add_parser("matrix", help="run a JSON matrix config")
    matrix.add_argument("--config", type=Path, required=True)
    matrix.add_argument("--assets-root", type=Path, default=Path("tests/assets"))
    matrix.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT / "smoke")
    matrix.add_argument("--dry-run", action="store_true")
    matrix.set_defaults(handler=_run_matrix)
    return parser


def _add_pdf_source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", type=Path)
    _add_work_dir(parser, "isolated run directory")
    parser.add_argument("--ocr-mode", choices=_ocr_modes(), help="overrides PDF_CRAFT_OCR_MODE")


def _add_work_dir(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument("--work-dir", type=Path, help=f"{help_text}; defaults under {DEFAULT_OUTPUT_ROOT}/manual")


def _add_extraction_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pages", help="comma-separated 1-based PDF page indexes")
    parser.add_argument("--ocr-size", choices=("tiny", "small", "base", "large", "gundam"), default="gundam")
    parser.add_argument("--dpi", type=int)
    parser.add_argument("--max-page-image-file-size", type=int)
    parser.add_argument("--max-ocr-tokens", type=int)
    parser.add_argument("--max-ocr-output-tokens", type=int)
    parser.add_argument("--cover", action="store_true")
    parser.add_argument("--footnotes", action="store_true")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--toc-assumed", action="store_true")
    parser.add_argument("--toc-llm", metavar="PROFILE", help="optional LLM profile for TOC hierarchy analysis")


def _add_translation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--submit", choices=("replace", "append-block"), default="replace")
    parser.add_argument("--prompt")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-group-tokens", type=int, default=2600)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--translation-llm", default="translation", metavar="PROFILE")
    parser.add_argument("--fill-llm", default="fill", metavar="PROFILE")


def _add_smoke_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pages", help="comma-separated 1-based PDF page indexes")
    parser.add_argument("--ocr-size", choices=("tiny", "small", "base", "large", "gundam"), default="tiny")
    parser.add_argument("--dpi", type=int)
    parser.add_argument("--max-page-image-file-size", type=int)
    parser.add_argument("--max-ocr-tokens", type=int)
    parser.add_argument("--max-ocr-output-tokens", type=int)
    parser.add_argument("--cover", action="store_true")
    parser.add_argument("--footnotes", action="store_true")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--toc-assumed", action="store_true")
    parser.add_argument("--marker", help="deterministic package-transform marker for Markdown or EPUB routes")
    parser.add_argument("--submit", choices=("replace", "append-block"), default="replace")
    parser.add_argument("--patch-prefix", help="deterministic prefix used by the PDF patch smoke route")
    parser.add_argument("--target-language", default="zh")
    parser.add_argument("--prompt")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-group-tokens", type=int, default=2600)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--translation-llm-profile", metavar="PROFILE")
    parser.add_argument("--fill-llm-profile", metavar="PROFILE")


def _extract_pdf(args: argparse.Namespace) -> None:
    work_dir = _work_dir(args.source, args.work_dir, "extract")
    result = _extract(args, work_dir / "package")
    print(f"Package: {result.package.chapters_path.parent}")
    _print_metering(result.metering)


def _convert_pdf(args: argparse.Namespace) -> None:
    work_dir = _work_dir(args.source, args.work_dir, "convert")
    result = _extract(args, work_dir / "package")
    output = args.output or work_dir / ("book.md" if args.format == "markdown" else "book.epub")
    output.parent.mkdir(parents=True, exist_ok=True)
    _render(result.craft, result.package, args.format, output)
    print(f"Package: {result.package.chapters_path.parent}")
    print(f"Output: {output}")
    _print_metering(result.metering)


def _translate_pdf(args: argparse.Namespace) -> None:
    if args.format == "pdf" and args.submit != "replace":
        raise SystemExit("PDF output supports only --submit replace")
    work_dir = _work_dir(args.source, args.work_dir, "translate")
    result = _extract(args, work_dir / "package")
    transformer = _xml_transformer(args, work_dir)
    if args.format == "pdf":
        output = args.output or work_dir / f"{args.source.stem}-{args.target_language}.pdf"
        output.parent.mkdir(parents=True, exist_ok=True)
        result.craft.translate_pdf(args.source, result.package, output, transformer)
    else:
        mode = SubmitKind[args.submit.replace("-", "_").upper()]
        translated = result.craft.transform_package(
            result.package, work_dir / "translated", (TranslationStep(ChapterPackageTransformer(transformer), mode),)
        )
        output = args.output or work_dir / ("book.md" if args.format == "markdown" else "book.epub")
        output.parent.mkdir(parents=True, exist_ok=True)
        _render(result.craft, translated, args.format, output)
    print(f"Package: {result.package.chapters_path.parent}")
    print(f"Output: {output}")
    _print_metering(result.metering)


def _render_package(args: argparse.Namespace) -> None:
    work_dir = _work_dir(args.package, args.work_dir, "render")
    package = DocumentPackage.from_path(args.package).validate()
    output = args.output or work_dir / ("book.md" if args.format == "markdown" else "book.epub")
    output.parent.mkdir(parents=True, exist_ok=True)
    _render(PDFCraft(), package, args.format, output)
    print(f"Output: {output}")


def _translate_epub(args: argparse.Namespace) -> None:
    load_project_env(_project_root())
    work_dir = _work_dir(args.source, args.work_dir, "translate")
    output = args.output or work_dir / "book.epub"
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    translation_llm = create_llm_from_env(args.translation_llm,
        cache_path=work_dir / "translation-cache", log_dir_path=work_dir / "translation-logs")
    fill_llm = translation_llm if args.fill_llm == args.translation_llm else create_llm_from_env(args.fill_llm,
        cache_path=work_dir / "fill-cache", log_dir_path=work_dir / "fill-logs")
    PDFCraft().translate_epub(
        args.source, output, target_language=args.target_language,
        submit=SubmitKind[args.submit.replace("-", "_").upper()],
        user_prompt=args.prompt, max_retries=args.max_retries,
        max_group_tokens=args.max_group_tokens, concurrency=args.concurrency,
        translation_llm=translation_llm, fill_llm=fill_llm,
    )
    print(f"Output: {output}")


def _list_assets(args: argparse.Namespace) -> None:
    print(json.dumps([
        asset.__dict__ | {"path": str(asset.path)} for asset in discover_assets(args.assets_root)
    ], ensure_ascii=False, indent=2))


def _run_smoke(args: argparse.Namespace) -> int:
    translation: dict[str, Any] = {}
    if args.marker:
        translation["package_marker"] = args.marker
        translation["package_submit"] = args.submit.replace("-", "_").upper()
    if args.patch_prefix:
        translation["patch_prefix"] = args.patch_prefix
    is_pdf_route = args.route in {
        "package", "package-markdown", "package-epub", "markdown", "epub", "pdf-patch",
    }
    if (is_pdf_route or args.route == "epub-translate") and not args.dry_run:
        load_project_env(_project_root())
    ocr_mode = cast(OCRMode | None, args.ocr_mode)
    if is_pdf_route:
        ocr_mode = ocr_mode or ocr_mode_from_env()
    if args.route == "epub-translate" or args.translation_llm_profile or args.fill_llm_profile:
        translation_profile = args.translation_llm_profile or "translation"
        fill_profile = args.fill_llm_profile or translation_profile
        translation.update({
            "translation_llm_profile": translation_profile,
            "fill_llm_profile": fill_profile,
            "target_language": args.target_language,
            "user_prompt": args.prompt,
            "max_retries": args.max_retries,
            "max_group_tokens": args.max_group_tokens,
            "concurrency": args.concurrency,
            "submit": args.submit.replace("-", "_").upper(),
        })
        if not args.dry_run:
            translation = _resolve_translation_profiles(translation, args.output_root) or {}
    run = SmokeRun(
        asset=args.asset, route=args.route, backend=ocr_mode,
        page_indexes=_page_indexes(args.pages), ocr_size=args.ocr_size, dpi=args.dpi,
        max_page_image_file_size=args.max_page_image_file_size,
        max_ocr_tokens=args.max_ocr_tokens, max_ocr_output_tokens=args.max_ocr_output_tokens,
        includes_cover=args.cover, includes_footnotes=args.footnotes,
        generate_plot=args.plot, toc_assumed=args.toc_assumed,
        ocr=ocr_values_from_env(ocr_mode) if ocr_mode and not args.dry_run else None,
        translation=translation or None,
    )
    run_path = run_smoke(run, assets_root=args.assets_root, output_root=args.output_root, dry_run=args.dry_run)
    print(run_path)
    return _smoke_exit_code(run_path)


def _run_matrix(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    runs = expand_matrix(config, args.assets_root)
    env_error: str | None = None
    if any(_matrix_run_needs_env(run) for run in runs):
        try:
            load_project_env(_project_root())
        except SystemExit as error:
            env_error = str(error)
    exit_code = 0
    for run in runs:
        if env_error and _matrix_run_needs_env(run):
            run = replace(run, configuration_error=env_error)
        else:
            try:
                run = _resolve_matrix_runtime(run, args.output_root)
            except SystemExit as error:
                run = replace(run, configuration_error=str(error))
        run_path = run_smoke(run, assets_root=args.assets_root, output_root=args.output_root, dry_run=args.dry_run)
        print(run_path)
        exit_code = max(exit_code, _smoke_exit_code(run_path))
    return exit_code


def _smoke_exit_code(run_path: Path) -> int:
    """Return a non-zero code for failed or skipped required smoke runs."""
    try:
        status = json.loads((run_path / "checks.json").read_text(encoding="utf-8"))["status"]
    except (OSError, KeyError, json.JSONDecodeError):
        return 1
    return 0 if status in {"passed", "planned"} else 1


def _matrix_run_needs_env(run: SmokeRun) -> bool:
    if run.backend and run.ocr is None:
        return True
    translation = run.translation or {}
    return any(
        key in translation
        for key in ("llm_profile", "translation_llm_profile", "fill_llm_profile")
    )


def _resolve_matrix_runtime(run: SmokeRun, output_root: Path) -> SmokeRun:
    ocr = run.ocr
    if run.backend and ocr is None:
        ocr = ocr_values_from_env(run.backend)
    translation = _resolve_translation_profiles(run.translation, output_root)
    return replace(run, ocr=ocr, translation=translation)


def _resolve_translation_profiles(translation: dict[str, Any] | None, output_root: Path) -> dict[str, Any] | None:
    if not translation:
        return translation
    resolved = dict(translation)
    profile = resolved.pop("llm_profile", None)
    translation_profile = resolved.pop("translation_llm_profile", profile)
    fill_profile = resolved.pop("fill_llm_profile", profile)
    if translation_profile and "llm" not in resolved:
        resolved["llm"] = llm_values_from_env(
            str(translation_profile),
            cache_path=output_root / "_c" / "t",
            log_dir_path=output_root / "_l" / "t",
        )
    if fill_profile and fill_profile != translation_profile and "fill_llm" not in resolved:
        resolved["fill_llm"] = llm_values_from_env(
            str(fill_profile),
            cache_path=output_root / "_c" / "f",
            log_dir_path=output_root / "_l" / "f",
        )
    return resolved


def _extract(args: argparse.Namespace, package_path: Path) -> _ExtractionResult:
    load_project_env(_project_root())
    craft = PDFCraft(pdf=PDFOptions(ocr=create_ocr_config_from_env(cast(OCRMode | None, args.ocr_mode))))
    package, metering = craft.extract_pdf_with_metering(
        args.source, package_path, ExtractionOptions(
            page_indexes=_page_indexes(args.pages), ocr_size=args.ocr_size, dpi=args.dpi,
            max_page_image_file_size=args.max_page_image_file_size,
            max_ocr_tokens=args.max_ocr_tokens, max_ocr_output_tokens=args.max_ocr_output_tokens,
            includes_cover=args.cover, includes_footnotes=args.footnotes,
            generate_plot=args.plot, toc_assumed=args.toc_assumed,
            toc_llm=(create_llm_from_env(args.toc_llm, cache_path=package_path.parent / "toc-cache",
                log_dir_path=package_path.parent / "toc-logs") if args.toc_llm else None),
            on_ocr_event=_print_ocr_event,
        )
    )
    return _ExtractionResult(craft, package, metering)


def _xml_transformer(args: argparse.Namespace, work_dir: Path) -> ChapterXMLTransformer:
    translation_llm = create_llm_from_env(args.translation_llm,
        cache_path=work_dir / "translation-cache", log_dir_path=work_dir / "translation-logs")
    fill_llm = translation_llm if args.fill_llm == args.translation_llm else create_llm_from_env(args.fill_llm,
        cache_path=work_dir / "fill-cache", log_dir_path=work_dir / "fill-logs")
    translator = XMLTranslator(
        translation_llm=translation_llm, fill_llm=fill_llm, target_language=args.target_language,
        user_prompt=args.prompt, ignore_translated_error=False, max_retries=args.max_retries,
        max_fill_displaying_errors=3, max_group_score=args.max_group_tokens,
        cache_seed_content=f"pdf-craft-tool:{args.target_language}",
    )
    return ChapterXMLTransformer(cast(Any, translator))


def _render(craft: PDFCraft, package: DocumentPackage, format_name: str, output: Path) -> None:
    if format_name == "markdown":
        craft.render_markdown(package, output, Path("assets"))
    else:
        craft.render_epub(package, output)


def _work_dir(source: Path, requested: Path | None, operation: str) -> Path:
    if requested is not None:
        path = requested
        path.mkdir(parents=True, exist_ok=False)
        return path
    return create_run_directory(DEFAULT_OUTPUT_ROOT / "manual", f"{source.stem}-{operation}")


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


def _ocr_modes() -> tuple[str, ...]:
    return (
        "deepseek-ocr-local", "deepseek-ocr2-local", "unlimited-ocr-local",
        "deepseek-ocr-vendor", "deepseek-ocr2-vendor", "unlimited-ocr-vendor",
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _print_ocr_event(event) -> None:
    print(f"OCR {event.kind.name.lower()}: page {event.page_index}/{event.total_pages}")


def _print_metering(metering) -> None:
    print(f"OCR tokens: input={metering.input_tokens}, output={metering.output_tokens}")
