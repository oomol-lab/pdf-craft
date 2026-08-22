import json
import platform
import shutil
import traceback
import zipfile
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from pdf_craft.craft import ExtractionOptions, PDFCraft, PDFOptions, TranslationStep
from pdf_craft.ocr_config import OCRConfig, OCRMode
from pdf_craft.transformer import SubmitKind
from pdf_craft.sequence.chapter import BlockLayout, BlockMember, Chapter, HTMLTag, ParagraphLayout
from pdf_craft.llm import LLM
from pdf_craft.pdf import OCREvent

from .assets import SmokeAsset, discover_assets
from .checks import check_epub, check_markdown, check_package, check_pdf_patch_geometry
from .ocr import create_ocr_config
from ..paths import DEFAULT_OUTPUT_ROOT, create_run_directory

SmokeRoute = Literal["package", "markdown", "epub", "pdf-patch", "epub-check", "epub-translate"]
PDF_ROUTES = {"package", "markdown", "epub", "pdf-patch"}
EPUB_ROUTES = {"epub-check", "epub-translate"}


@dataclass(frozen=True)
class SmokeRun:
    asset: str
    route: SmokeRoute
    backend: OCRMode | None = None
    page_indexes: tuple[int, ...] | None = None
    ocr_size: str = "gundam"
    dpi: int | None = None
    max_page_image_file_size: int | None = None
    max_ocr_tokens: int | None = None
    max_ocr_output_tokens: int | None = None
    includes_cover: bool = False
    includes_footnotes: bool = False
    generate_plot: bool = False
    toc_assumed: bool = False
    ocr: dict[str, Any] | None = None
    translation: dict[str, Any] | None = None


@dataclass
class _ExecutionReport:
    stages: list[dict[str, Any]] = field(default_factory=list)
    ocr_events: list[dict[str, Any]] = field(default_factory=list)
    secret_values: tuple[str, ...] = ()
    current_stage: str = "configure"

    @contextmanager
    def stage(self, name: str):
        self.current_stage = name
        entry: dict[str, Any] = {
            "stage": name, "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        }
        self.stages.append(entry)
        started = perf_counter()
        try:
            yield
        except Exception:
            entry["status"] = "failed"
            raise
        else:
            entry["status"] = "passed"
        finally:
            entry["finished_at"] = datetime.now(UTC).isoformat()
            entry["elapsed_seconds"] = round(perf_counter() - started, 3)

    def skipped(self, name: str, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        self.stages.append({"stage": name, "started_at": now, "finished_at": now,
                            "elapsed_seconds": 0.0, "status": "skipped", "reason": reason})

    def on_ocr_event(self, event: OCREvent) -> None:
        payload: dict[str, Any] = {
            "kind": event.kind.name.lower(), "page_index": event.page_index,
            "total_pages": event.total_pages, "cost_time_ms": event.cost_time_ms,
            "input_tokens": event.input_tokens, "output_tokens": event.output_tokens,
        }
        if event.error is not None:
            payload["error_type"] = type(event.error).__name__
            payload["error"] = self.redact_text(str(event.error))
        self.ocr_events.append(payload)

    def redact_text(self, text: str) -> str:
        for value in self.secret_values:
            text = text.replace(value, "[redacted]")
        return text


def expand_matrix(config: dict[str, Any], assets_root: Path) -> list[SmokeRun]:
    known = {asset.name: asset for asset in discover_assets(assets_root)}
    defaults = dict(config.get("defaults", {}))
    runs: list[SmokeRun] = []
    for item in config.get("runs", []):
        item = defaults | item
        asset = item["asset"]
        if asset not in known:
            raise ValueError(f"unknown smoke asset: {asset}")
        route = item["route"]
        if known[asset].format == "pdf" and route not in PDF_ROUTES:
            raise ValueError(f"PDF asset {asset} cannot use route {route}")
        if known[asset].format == "epub" and route not in EPUB_ROUTES:
            raise ValueError(f"EPUB asset {asset} cannot use route {route}")
        pages = item.get("page_indexes")
        runs.append(SmokeRun(**(item | {"page_indexes": tuple(pages) if pages else None})))
    return runs


def run_smoke(
    run: SmokeRun,
    *,
    assets_root: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT / "smoke",
    dry_run: bool = False,
) -> Path:
    assets = {asset.name: asset for asset in discover_assets(assets_root)}
    asset = assets.get(run.asset)
    if asset is None:
        raise ValueError(f"unknown smoke asset: {run.asset}")
    run_path = create_run_directory(output_root, f"{Path(run.asset).stem}-{run.route}")
    (run_path / "package").mkdir()
    (run_path / "output").mkdir()
    manifest = _manifest(run, asset, dry_run)
    _write_json(run_path / "manifest.json", manifest)
    (run_path / "logs").mkdir()
    if dry_run:
        _finish(run_path, manifest, "planned", [])
        return run_path
    started = perf_counter()
    report = _ExecutionReport(secret_values=tuple(_secret_values(run)))
    try:
        status, errors, details = _execute(run, asset, run_path, report)
    except Exception as error:  # preserve the full failure report for manual inspection
        traceback_path = run_path / "logs" / "traceback.txt"
        traceback_path.write_text(report.redact_text(traceback.format_exc()), encoding="utf-8")
        message = report.redact_text(str(error))
        status, errors, details = "failed", [message], {
            "failure": {"stage": report.current_stage, "exception_type": type(error).__name__,
                        "message": message, "traceback_path": str(traceback_path.relative_to(run_path))}
        }
    manifest["elapsed_seconds"] = round(perf_counter() - started, 3)
    manifest.update(details)
    manifest["timeline"] = report.stages
    manifest["ocr_events"] = report.ocr_events
    with report.stage("finish"):
        pass
    _finish(run_path, manifest, status, [report.redact_text(error) for error in errors])
    return run_path


def _execute(run: SmokeRun, asset: SmokeAsset, run_path: Path,
             report: _ExecutionReport) -> tuple[str, list[str], dict[str, Any]]:
    if asset.format == "epub":
        output = run_path / "output" / "book.epub"
        report.skipped("configure", "EPUB routes do not require OCR configuration")
        report.skipped("extract", "EPUB routes do not extract a DocumentPackage")
        if run.route == "epub-check":
            with report.stage("render"):
                shutil.copy2(asset.path, output)
            with report.stage("check"):
                status, errors = _result_from_errors(check_epub(output))
            return status, errors, {"outputs": [str(output)]}
        if not run.translation or not isinstance(run.translation.get("llm"), dict):
            report.skipped("render", "missing translation.llm")
            report.skipped("check", "translation was not run")
            return "skipped", ["EPUB translation requires translation.llm with explicit credentials"], {}
        with report.stage("render"):
            llm = LLM(**run.translation["llm"])
            submit = SubmitKind[run.translation.get("submit", "REPLACE").upper()]
            PDFCraft().translate_epub(asset.path, output, target_language=run.translation.get("target_language", "zh"),
                                      submit=submit, user_prompt=run.translation.get("user_prompt"),
                                      max_retries=run.translation.get("max_retries", 5),
                                      max_group_tokens=run.translation.get("max_group_tokens", 2600),
                                      concurrency=run.translation.get("concurrency", 1), llm=llm)
        with report.stage("check"):
            status, errors = _result_from_errors(check_epub(output))
        return status, errors, {"outputs": [str(output)]}

    if run.backend is None or run.ocr is None:
        report.skipped("configure", "missing OCR backend configuration")
        report.skipped("extract", "missing OCR backend configuration")
        report.skipped("render", "missing OCR backend configuration")
        report.skipped("check", "missing OCR backend configuration")
        return "skipped", ["PDF route requires an explicit OCR backend"], {}
    with report.stage("configure"):
        ocr = create_ocr_config(run.backend, run.ocr)
    return _run_pdf(run, asset, run_path, ocr, report)


def _run_pdf(
    run: SmokeRun, asset: SmokeAsset, run_path: Path, ocr: OCRConfig,
    report: _ExecutionReport | None = None,
) -> tuple[str, list[str], dict[str, Any]]:
    package_path = run_path / "package"
    output_path = run_path / "output"
    craft = PDFCraft(pdf=PDFOptions(ocr=ocr))
    try:
        report = report or _ExecutionReport()
        with report.stage("extract"):
            package, metering = craft.extract_pdf_with_metering(
                asset.path, package_path, ExtractionOptions(
                    page_indexes=run.page_indexes, ocr_size=cast(Any, run.ocr_size), dpi=run.dpi,
                    max_page_image_file_size=run.max_page_image_file_size,
                    max_ocr_tokens=run.max_ocr_tokens,
                    max_ocr_output_tokens=run.max_ocr_output_tokens,
                    includes_cover=run.includes_cover,
                    includes_footnotes=run.includes_footnotes,
                generate_plot=run.generate_plot, toc_assumed=run.toc_assumed,
                on_ocr_event=report.on_ocr_event,
                )
            )
    except Exception as error:
        unavailable = _unavailable_ocr_reason(error)
        if unavailable is not None:
            return "skipped", [unavailable], {"package": str(package_path)}
        raise
    details = {
        "package": str(package_path),
        "metering": {"input_tokens": metering.input_tokens, "output_tokens": metering.output_tokens},
    }
    if run.route == "package":
        report.skipped("render", "package route has no renderer")
        with report.stage("check"):
            errors = check_package(package, require_geometry=False)
            status, errors = _result_from_errors(errors)
        return status, errors, details
    if run.route == "markdown":
        markdown = output_path / "book.md"
        markdown_assets = Path("assets")
        steps = _package_steps(run)
        with report.stage("render"):
            if steps:
                package = craft.transform_package(package, run_path / "translated", steps)
            craft.render_markdown(package, markdown, markdown_assets)
        with report.stage("check"):
            errors = check_package(package)
            errors.extend(check_markdown(markdown))
            marker = (run.translation or {}).get("package_marker")
            if isinstance(marker, str) and markdown.is_file() and marker not in markdown.read_text(encoding="utf-8"):
                errors.append(f"Package translation marker missing from Markdown: {marker}")
        details["outputs"] = [str(markdown)]
        status, errors = _result_from_errors(errors)
        return status, errors, details
    if run.route == "epub":
        epub = output_path / "book.epub"
        steps = _package_steps(run)
        with report.stage("render"):
            if steps:
                package = craft.transform_package(package, run_path / "translated", steps)
            craft.render_epub(package, epub)
        with report.stage("check"):
            errors = check_package(package)
            errors.extend(check_epub(epub))
            marker = (run.translation or {}).get("package_marker")
            if isinstance(marker, str) and not _epub_contains_marker(epub, marker):
                errors.append(f"Package translation marker missing from EPUB: {marker}")
        details["outputs"] = [str(epub)]
        status, errors = _result_from_errors(errors)
        return status, errors, details
    prefix = (run.translation or {}).get("patch_prefix")
    if not isinstance(prefix, str):
        report.skipped("render", "missing PDF patch transformer")
        with report.stage("check"):
            errors = check_package(package, require_geometry=True)
            errors.extend(check_pdf_patch_geometry(package))
        return "skipped", errors + ["PDF patching requires translation.patch_prefix or an application transformer"], details
    target = output_path / "book.pdf"
    with report.stage("render"):
        craft.translate_pdf(asset.path, package, target, lambda text: prefix + text)
    from .checks import check_pdf
    import pypdf
    with report.stage("check"):
        errors = check_package(package, require_geometry=True)
        errors.extend(check_pdf_patch_geometry(package))
        errors.extend(check_pdf(target, len(pypdf.PdfReader(str(asset.path)).pages)))
    details["outputs"] = [str(target)]
    status, errors = _result_from_errors(errors)
    return status, errors, details


def _unavailable_ocr_reason(error: Exception) -> str | None:
    """Recognise infrastructure gaps without hiding extraction failures."""
    current: BaseException | None = error
    while current is not None:
        if "No CUDA devices available" in str(current):
            return "OCR backend unavailable: local OCR requires CUDA, but no CUDA device is available"
        current = current.__cause__ or current.__context__
    return None


def _epub_contains_marker(epub: Path, marker: str) -> bool:
    if not zipfile.is_zipfile(epub):
        return False
    encoded_marker = marker.encode("utf-8")
    with zipfile.ZipFile(epub) as archive:
        return any(
            encoded_marker in archive.read(name)
            for name in archive.namelist()
            if name.lower().endswith((".xhtml", ".html"))
        )


class _DeterministicChapterTransformer:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.mode = SubmitKind.REPLACE

    def with_mode(self, mode: SubmitKind) -> "_DeterministicChapterTransformer":
        transformer = _DeterministicChapterTransformer(self.marker)
        transformer.mode = mode
        return transformer

    def transform(self, chapter: Chapter) -> Chapter:
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout):
                continue
            transformed_blocks: list[BlockLayout] = []
            for block in layout.blocks:
                translated = BlockLayout(
                    page_index=block.page_index,
                    order=block.order,
                    det=block.det,
                    content=[self._transform_item(item) for item in deepcopy(block.content)],
                )
                if self.mode == SubmitKind.APPEND_BLOCK:
                    transformed_blocks.extend((block, translated))
                else:
                    transformed_blocks.append(translated)
            layout.blocks = transformed_blocks
        return chapter

    def _transform_item(self, item: str | BlockMember | HTMLTag[BlockMember]):
        if isinstance(item, str):
            return item + self.marker
        if isinstance(item, HTMLTag):
            item.children = [self._transform_item(child) for child in item.children]
        return item


def _package_steps(run: SmokeRun):
    translation = run.translation or {}
    marker = translation.get("package_marker")
    if not isinstance(marker, str):
        return ()
    mode = SubmitKind[translation.get("package_submit", "REPLACE").upper()]
    from pdf_craft.transformer import ChapterPackageTransformer
    return (TranslationStep(ChapterPackageTransformer(_DeterministicChapterTransformer(marker)), mode),)


def _result_from_errors(errors: list[str]) -> tuple[str, list[str]]:
    return ("passed" if not errors else "failed", errors)


def _manifest(run: SmokeRun, asset: SmokeAsset, dry_run: bool) -> dict[str, Any]:
    return {
        "schema": 1,
        "run": _redact(asdict(run)),
        "asset": {"name": asset.name, "format": asset.format, "path": str(asset.path)},
        "started_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
    }


def _finish(run_path: Path, manifest: dict[str, Any], status: str, errors: list[str]) -> None:
    manifest["status"] = status
    manifest["finished_at"] = datetime.now(UTC).isoformat()
    _write_json(run_path / "manifest.json", manifest)
    _write_json(run_path / "checks.json", {"status": status, "errors": errors,
                                             "timeline": manifest.get("timeline", []),
                                             "failure": manifest.get("failure")})


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_secret_key(key)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in {
        "ak", "sk", "api_key", "apikey", "access_key", "secret_key", "password", "secret",
        "token", "api_token", "access_token", "refresh_token", "auth_token", "bearer_token",
    }:
        return True
    return (
        normalized.startswith(("secret_", "password_", "credential_"))
        or normalized.endswith(("_secret", "_password", "_credential", "_token", "_key"))
    )


def _secret_values(run: SmokeRun) -> list[str]:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _is_secret_key(key) and isinstance(item, str) and item:
                    values.append(item)
                else:
                    visit(item)
        elif isinstance(value, list | tuple):
            for item in value:
                visit(item)

    visit(run.ocr)
    visit(run.translation)
    return sorted(set(values), key=len, reverse=True)
