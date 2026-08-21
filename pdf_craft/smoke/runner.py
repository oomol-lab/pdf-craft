import json
import platform
import shutil
import traceback
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from pdf_craft.craft import ExtractionOptions, PDFCraft, PDFOptions, TranslationStep
from pdf_craft.ocr_config import OCRConfig, OCRMode
from pdf_craft.transformer import SubmitKind
from pdf_craft.sequence.chapter import BlockLayout, BlockMember, Chapter, HTMLTag, ParagraphLayout
from pdf_craft.llm import LLM

from .assets import SmokeAsset, discover_assets
from .checks import check_epub, check_markdown, check_package, check_pdf_patch_geometry
from .ocr import create_ocr_config

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
    output_root: Path = Path("analysing/smoke"),
    dry_run: bool = False,
) -> Path:
    assets = {asset.name: asset for asset in discover_assets(assets_root)}
    asset = assets.get(run.asset)
    if asset is None:
        raise ValueError(f"unknown smoke asset: {run.asset}")
    run_path = output_root / _run_id(run)
    run_path.mkdir(parents=True, exist_ok=False)
    (run_path / "package").mkdir()
    (run_path / "output").mkdir()
    manifest = _manifest(run, asset, dry_run)
    _write_json(run_path / "manifest.json", manifest)
    (run_path / "logs").mkdir()
    if dry_run:
        _finish(run_path, manifest, "planned", [])
        return run_path
    started = perf_counter()
    try:
        status, errors, details = _execute(run, asset, run_path)
    except Exception as error:  # preserve the full failure report for manual inspection
        (run_path / "logs" / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        status, errors, details = "failed", [str(error)], {}
    manifest["elapsed_seconds"] = round(perf_counter() - started, 3)
    manifest.update(details)
    _finish(run_path, manifest, status, errors)
    return run_path


def _execute(run: SmokeRun, asset: SmokeAsset, run_path: Path) -> tuple[str, list[str], dict[str, Any]]:
    if asset.format == "epub":
        output = run_path / "output" / "book.epub"
        if run.route == "epub-check":
            shutil.copy2(asset.path, output)
            status, errors = _result_from_errors(check_epub(output))
            return status, errors, {"outputs": [str(output)]}
        if not run.translation or not isinstance(run.translation.get("llm"), dict):
            return "skipped", ["EPUB translation requires translation.llm with explicit credentials"], {}
        llm = LLM(**run.translation["llm"])
        submit = SubmitKind[run.translation.get("submit", "REPLACE").upper()]
        PDFCraft().translate_epub(
            asset.path,
            output,
            target_language=run.translation.get("target_language", "zh"),
            submit=submit,
            user_prompt=run.translation.get("user_prompt"),
            max_retries=run.translation.get("max_retries", 5),
            max_group_tokens=run.translation.get("max_group_tokens", 2600),
            concurrency=run.translation.get("concurrency", 1),
            llm=llm,
        )
        status, errors = _result_from_errors(check_epub(output))
        return status, errors, {"outputs": [str(output)]}

    if run.backend is None or run.ocr is None:
        return "skipped", ["PDF route requires an explicit OCR backend"], {}
    return _run_pdf(run, asset, run_path, create_ocr_config(run.backend, run.ocr))


def _run_pdf(
    run: SmokeRun, asset: SmokeAsset, run_path: Path, ocr: OCRConfig
) -> tuple[str, list[str], dict[str, Any]]:
    package_path = run_path / "package"
    output_path = run_path / "output"
    craft = PDFCraft(pdf=PDFOptions(ocr=ocr))
    try:
        package, metering = craft.extract_pdf_with_metering(
            asset.path, package_path, ExtractionOptions(
                page_indexes=run.page_indexes, ocr_size=cast(Any, run.ocr_size), dpi=run.dpi,
                max_page_image_file_size=run.max_page_image_file_size,
                max_ocr_tokens=run.max_ocr_tokens,
                max_ocr_output_tokens=run.max_ocr_output_tokens,
                includes_cover=run.includes_cover,
                includes_footnotes=run.includes_footnotes,
                generate_plot=run.generate_plot, toc_assumed=run.toc_assumed,
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
    errors = check_package(package, require_geometry=run.route == "pdf-patch")
    if run.route == "pdf-patch":
        errors.extend(check_pdf_patch_geometry(package))
    if errors and run.route == "pdf-patch":
        return "failed", errors, details
    if run.route == "package":
        status, errors = _result_from_errors(errors)
        return status, errors, details
    if run.route == "markdown":
        markdown = output_path / "book.md"
        markdown_assets = output_path / "assets"
        steps = _package_steps(run)
        if steps:
            package = craft.transform_package(package, run_path / "translated", steps)
        craft.render_markdown(package, markdown, markdown_assets)
        errors.extend(check_markdown(markdown, markdown_assets))
        marker = (run.translation or {}).get("package_marker")
        if isinstance(marker, str) and marker not in markdown.read_text(encoding="utf-8"):
            errors.append(f"Package translation marker missing from Markdown: {marker}")
        details["outputs"] = [str(markdown)]
        status, errors = _result_from_errors(errors)
        return status, errors, details
    if run.route == "epub":
        epub = output_path / "book.epub"
        steps = _package_steps(run)
        if steps:
            package = craft.transform_package(package, run_path / "translated", steps)
        craft.render_epub(package, epub)
        errors.extend(check_epub(epub))
        details["outputs"] = [str(epub)]
        status, errors = _result_from_errors(errors)
        return status, errors, details
    prefix = (run.translation or {}).get("patch_prefix")
    if not isinstance(prefix, str):
        return "skipped", errors + ["PDF patching requires translation.patch_prefix or an application transformer"], details
    target = output_path / "book.pdf"
    craft.translate_pdf(asset.path, package, target, lambda text: prefix + text)
    from .checks import check_pdf
    import pypdf
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
    _write_json(run_path / "checks.json", {"status": status, "errors": errors})


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if any(part in key.lower() for part in ("key", "secret", "token", "password"))
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def _run_id(run: SmokeRun) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{Path(run.asset).stem}-{run.route}-{uuid.uuid4().hex[:8]}"
