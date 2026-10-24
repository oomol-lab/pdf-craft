"""Mandatory, task-local compatibility checks for both PDF backends.

This is not a PDF standards validator and never rewrites the source document.
"""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import inspect
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar, cast

from ..error import PDFPreflightError
from ..runtime import IO_DOMAIN, run_cancellable
from .page_labels import read_page_labels
from .poppler import PageGeometry, PopplerDocument


@dataclass(frozen=True)
class PreflightResult:
    pages: dict[int, PageGeometry]
    labels: dict[int, str]


# Each top-level PDF operation owns this mapping; no successes survive a call.
_results: ContextVar[dict[tuple, PreflightResult] | None] = ContextVar("pdf_preflight", default=None)
F = TypeVar("F", bound=Callable[..., Any])


def _key(source: Path) -> tuple:
    stat = source.stat()
    return str(source.resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _failure(source: Path, backend: str, stage: str, page_index: int | None, action):
    try:
        return action()
    except PDFPreflightError:
        raise
    except Exception as error:
        raise PDFPreflightError(str(source), backend, stage, page_index, str(error)) from error


def _read_pypdf_page(reader, index: int) -> PageGeometry:
    page = reader.pages[index - 1]
    media = cast(tuple[float, float, float, float], tuple(float(value) for value in page.mediabox))
    crop = cast(tuple[float, float, float, float], tuple(float(value) for value in page.cropbox))
    rotation = int(page.rotation) % 360
    if rotation not in (0, 90, 180, 270) or not all(math.isfinite(value) for value in (*media, *crop)):
        raise ValueError("Invalid PDF page geometry")
    if media[2] <= media[0] or media[3] <= media[1]:
        raise ValueError("Empty or inverted PDF MediaBox")
    # CropBox is the effective intersection with MediaBox in Poppler. An
    # overhanging declaration is not a backend disagreement.
    crop = (max(crop[0], media[0]), max(crop[1], media[1]),
            min(crop[2], media[2]), min(crop[3], media[3]))
    _resolve_resources(page.get("/Resources"), set())
    contents = page.get_contents()
    if contents is not None:
        contents.get_data()
    return PageGeometry(media, crop, rotation)


def _resolve_resources(value, seen: set[int]) -> None:
    """Resolve referenced resources without looping through shared PDF objects."""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, IndirectObject):
        _resolve_resources(value.get_object(), seen)
    elif isinstance(value, DictionaryObject):
        for child in value.values():
            _resolve_resources(child, seen)
    elif isinstance(value, ArrayObject):
        for child in value:
            _resolve_resources(child, seen)


def _check_geometry(expected: PageGeometry, actual: PageGeometry) -> None:
    # pdfinfo prints boxes to two decimal places. Compare raw boxes, never an
    # unrotated MediaBox with a rotated/cropped display-size shortcut.
    if expected.rotation != actual.rotation or any(
        abs(left - right) > 0.011
        for left, right in zip((*expected.media_box, *expected.crop_box),
                               (*actual.media_box, *actual.crop_box), strict=True)
    ):
        raise ValueError(f"PDF backends disagree on page geometry: {expected!r} vs {actual!r}")


def check_pdf(source: Path, aborted: Callable[[], bool] = lambda: False) -> PreflightResult:
    """Blocking worker boundary; callers normally use pdf_operation."""
    import asyncio
    from importlib import import_module
    pypdf = _failure(source, "pypdf", "open", None, lambda: import_module("pypdf"))

    def checkpoint():
        if aborted():
            raise asyncio.CancelledError()

    checkpoint()
    key = _failure(source, "pypdf", "open", None, lambda: _key(source))
    cache = _results.get()
    if cache is not None and key in cache:
        return cache[key]
    # Use the backend's strict reader. Never run a separate repair pass or
    # substitute a normalized PDF for the original input.
    reader = _failure(source, "pypdf", "open", None, lambda: pypdf.PdfReader(source, strict=True))
    poppler = PopplerDocument(source, aborted=aborted)
    try:
        count = _failure(source, "pypdf", "page_read", None, lambda: len(reader.pages))
        if count <= 0:
            raise PDFPreflightError(str(source), "pypdf", "page_read", None, "PDF has no pages")
        poppler_count = _failure(source, "poppler", "open", None, lambda: poppler.pages_count)
        if count != poppler_count:
            raise PDFPreflightError(str(source), "consistency", "page_count", None,
                                    f"pypdf reports {count} pages, Poppler reports {poppler_count}")
        pages = {}
        for index in range(1, count + 1):
            checkpoint()
            expected = _failure(source, "pypdf", "page_read", index, lambda index=index: _read_pypdf_page(reader, index))
            actual = _failure(source, "poppler", "page_read", index, lambda index=index: poppler.geometry(index))
            _failure(source, "consistency", "geometry", index, lambda expected=expected, actual=actual: _check_geometry(expected, actual))
            image = _failure(source, "poppler", "render", index, lambda index=index: poppler.render_page(index, 36))
            image.close()
            pages[index] = actual
        checkpoint()
        # Presence is structural, not a guessed comparison with physical page
        # numbers. Actual publication-label strings come from Poppler.
        explicit_labels = _failure(source, "pypdf", "page_read", None,
                                   lambda: reader.root_object.get("/PageLabels") is not None)
        labels = (_failure(source, "poppler", "page_labels", None,
                           lambda: read_page_labels(source, count, aborted)) if explicit_labels else {})
        with TemporaryDirectory(prefix="pdf-craft-preflight-") as directory:
            target = Path(directory) / "trial.pdf"
            def trial_write():
                writer = pypdf.PdfWriter(clone_from=reader)
                try:
                    writer.write(target)
                finally:
                    writer.close()
            _failure(source, "pypdf", "write", None, trial_write)
            checkpoint()
            trial = _failure(source, "pypdf", "reopen", None, lambda: pypdf.PdfReader(target, strict=True))
            try:
                if _failure(source, "pypdf", "reopen", None, lambda: len(trial.pages)) != count:
                    raise PDFPreflightError(str(source), "pypdf", "reopen", None, "Trial write changed page count")
                for index in range(1, count + 1):
                    checkpoint()
                    geometry = _failure(source, "pypdf", "reopen", index, lambda index=index: _read_pypdf_page(trial, index))
                    _failure(source, "pypdf", "reopen", index, lambda geometry=geometry, index=index: _check_geometry(geometry, pages[index]))
            finally:
                trial.close()
        checkpoint()
        if _failure(source, "pypdf", "open", None, lambda: _key(source)) != key:
            raise PDFPreflightError(str(source), "consistency", "source_changed", None,
                                    "Source PDF changed during preflight")
        result = PreflightResult(pages, labels)
        if cache is not None:
            cache[key] = result
        return result
    finally:
        reader.close()
        poppler.close()


def pdf_operation(function: F) -> F:
    """Gate nested PDF entry points once within their top-level task scope."""
    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args, **kwargs):
        bound = dict(signature.bind_partial(*args, **kwargs).arguments)
        values = {**bound, **bound.get("kwargs", {})}
        source = next(values[key] for key in ("source", "source_path", "pdf_path") if key in values)
        source = Path(source)
        token = _results.set({}) if _results.get() is None else None
        try:
            await run_cancellable(IO_DOMAIN, lambda aborted: check_pdf(source, aborted),
                                  original_aborted=values.get("aborted") or getattr(
                                      values.get("options") or values.get("extraction"), "aborted", None))
            return await function(*args, **kwargs)
        finally:
            if token is not None:
                _results.reset(token)
    return cast(F, invoke)
