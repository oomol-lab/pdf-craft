"""Parallel OCR: disjoint page ranges in separate processes, shared analysing_path."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
from typing import Sequence

from .error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .pdf import DeepSeekOCRSize, OCR, PDFHandler, pdf_pages_count
from .to_path import to_path


def partition_pages(total_pages: int, workers: int) -> list[list[int]]:
    """Split 1-based page indices into ``workers`` contiguous ranges as evenly as possible."""
    if total_pages < 1:
        return []
    if workers < 1:
        raise ValueError("workers must be >= 1")
    workers = min(workers, total_pages)
    base, extra = divmod(total_pages, workers)
    ranges: list[list[int]] = []
    start = 1
    for i in range(workers):
        size = base + (1 if i < extra else 0)
        if size < 1:
            break
        end = start + size - 1
        ranges.append(list(range(start, end + 1)))
        start = end + 1
    return ranges


@dataclass(frozen=True)
class _WorkerConfig:
    pdf_path: str
    asset_path: str
    ocr_path: str
    page_indexes: tuple[int, ...]
    models_cache_path: str | None
    local_only: bool
    ocr_size: DeepSeekOCRSize
    dpi: int | None
    max_page_image_file_size: int | None
    includes_footnotes: bool
    ignore_pdf_errors: object
    ignore_ocr_errors: object
    plot_path: str | None
    cover_path: str | None
    max_tokens: int | None
    max_output_tokens: int | None
    cuda_visible_device: str | None


def _worker_entry(cfg: _WorkerConfig) -> None:
    import sys

    if cfg.cuda_visible_device is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = cfg.cuda_visible_device

    ignore_pdf: IgnorePDFErrorsChecker = cfg.ignore_pdf_errors  # type: ignore[assignment]
    ignore_ocr: IgnoreOCRErrorsChecker = cfg.ignore_ocr_errors  # type: ignore[assignment]

    ocr = OCR(
        model_path=cfg.models_cache_path,
        pdf_handler=None,
        local_only=cfg.local_only,
    )
    plot_path = Path(cfg.plot_path) if cfg.plot_path else None
    cover_path = Path(cfg.cover_path) if cfg.cover_path else None
    try:
        for _ in ocr.recognize(
            pdf_path=Path(cfg.pdf_path),
            asset_path=Path(cfg.asset_path),
            ocr_path=Path(cfg.ocr_path),
            ocr_size=cfg.ocr_size,
            dpi=cfg.dpi,
            max_page_image_file_size=cfg.max_page_image_file_size,
            includes_footnotes=cfg.includes_footnotes,
            ignore_pdf_errors=ignore_pdf,
            ignore_ocr_errors=ignore_ocr,
            plot_path=plot_path,
            cover_path=cover_path,
            page_indexes=frozenset(cfg.page_indexes),
            max_tokens=cfg.max_tokens,
            max_output_tokens=cfg.max_output_tokens,
            device_number=0,
        ):
            pass
    except BaseException:
        traceback.print_exc()
        sys.exit(1)


def run_parallel_ocr(
    *,
    pdf_path: Path,
    analysing_path: Path,
    models_cache_path: Path | str | None,
    local_only: bool,
    workers: int,
    ocr_size: DeepSeekOCRSize = "gundam",
    dpi: int | None = None,
    max_page_image_file_size: int | None = None,
    includes_footnotes: bool = False,
    includes_cover: bool = False,
    generate_plot: bool = False,
    ignore_pdf_errors: IgnorePDFErrorsChecker = False,
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
    max_ocr_tokens: int | None = None,
    max_ocr_output_tokens: int | None = None,
    gpu_ids: Sequence[int] | None = None,
    pdf_handler: PDFHandler | None = None,
) -> None:
    """
    Run :meth:`OCR.recognize` over disjoint page sets in separate processes, writing into
    the same ``analysing_path`` layout as :class:`~pdf_craft.transform.Transform`.

    After this completes, call :func:`~pdf_craft.functions.transform_markdown` with the same
    paths; the OCR step will ``SKIP`` existing ``page_*.xml`` files, create ``ocr/done``, then
    run TOC and markdown rendering.

    * ``workers > 1`` requires ``pdf_handler is None`` (default handler in each process).
    * ``gpu_ids`` length must match ``workers`` when set; each worker sets
      ``CUDA_VISIBLE_DEVICES`` to that id before loading the model.
    * Token limits are split across workers (sum of per-worker caps is at most the original).
    """
    pdf_path = Path(pdf_path)
    analysing_path = Path(analysing_path)
    cache = to_path(models_cache_path) if models_cache_path is not None else None

    total = pdf_pages_count(pdf_path, pdf_handler)
    if total < 1:
        raise ValueError("PDF has no pages")

    if workers < 1:
        raise ValueError("workers must be >= 1")

    workers_eff = min(workers, total)
    parts = partition_pages(total, workers_eff)

    if gpu_ids is not None and len(gpu_ids) != workers_eff:
        raise ValueError(
            f"gpu_ids length ({len(gpu_ids)}) must match effective workers ({workers_eff})"
        )

    if workers_eff > 1 and pdf_handler is not None:
        raise ValueError("pdf_handler must be None when using more than one worker")

    asserts_path = analysing_path / "assets"
    pages_path = analysing_path / "ocr"
    asserts_path.mkdir(parents=True, exist_ok=True)
    pages_path.mkdir(parents=True, exist_ok=True)

    done_path = pages_path / "done"
    done_path.unlink(missing_ok=True)

    cover_path: Path | None = (analysing_path / "cover.png") if includes_cover else None
    plot_path: Path | None = None
    if generate_plot:
        plot_path = analysing_path / "plots"
        plot_path.mkdir(parents=True, exist_ok=True)

    per_tokens = max_ocr_tokens
    if per_tokens is not None:
        per_tokens = max(1, per_tokens // workers_eff)
    per_out = max_ocr_output_tokens
    if per_out is not None:
        per_out = max(1, per_out // workers_eff)

    if workers_eff == 1:
        ocr = OCR(model_path=cache, pdf_handler=pdf_handler, local_only=local_only)
        for _ in ocr.recognize(
            pdf_path=pdf_path,
            asset_path=asserts_path,
            ocr_path=pages_path,
            ocr_size=ocr_size,
            dpi=dpi,
            max_page_image_file_size=max_page_image_file_size,
            includes_footnotes=includes_footnotes,
            ignore_pdf_errors=ignore_pdf_errors,
            ignore_ocr_errors=ignore_ocr_errors,
            plot_path=plot_path,
            cover_path=cover_path,
            max_tokens=per_tokens,
            max_output_tokens=per_out,
        ):
            pass
        return

    ctx = get_context("spawn")
    processes: list = []
    for i, page_list in enumerate(parts):
        cuda_vis = str(gpu_ids[i]) if gpu_ids is not None else None
        cfg = _WorkerConfig(
            pdf_path=str(pdf_path.resolve()),
            asset_path=str(asserts_path.resolve()),
            ocr_path=str(pages_path.resolve()),
            page_indexes=tuple(page_list),
            models_cache_path=str(cache.resolve()) if cache is not None else None,
            local_only=local_only,
            ocr_size=ocr_size,
            dpi=dpi,
            max_page_image_file_size=max_page_image_file_size,
            includes_footnotes=includes_footnotes,
            ignore_pdf_errors=ignore_pdf_errors,
            ignore_ocr_errors=ignore_ocr_errors,
            plot_path=str(plot_path.resolve()) if plot_path else None,
            cover_path=str(cover_path.resolve()) if cover_path else None,
            max_tokens=per_tokens,
            max_output_tokens=per_out,
            cuda_visible_device=cuda_vis,
        )
        p = ctx.Process(target=_worker_entry, args=(cfg,))
        p.start()
        processes.append(p)

    failed: list[tuple[int, int | None]] = []
    for idx, p in enumerate(processes):
        p.join()
        if p.exitcode != 0:
            failed.append((idx, p.exitcode))

    if failed:
        raise RuntimeError(f"Parallel OCR worker(s) failed: (index, exitcode)={failed}")
