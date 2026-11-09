"""Page work settles locally; publication belongs to the operation."""
from __future__ import annotations
# pylint: disable=protected-access
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from contextvars import ContextVar
from contextlib import AsyncExitStack, asynccontextmanager
from functools import wraps

from .._progress import OperationProgress, DiskUnits, WorkUnit, isolated
from ..progress import OperationKind, UnitKind, UnitOutcome, RefinementStatus
from ..runtime import IO_DOMAIN, callback_bridge, temporary_directory
from ..pdf.ocr import OCREventKind

_active: ContextVar[ExtractionProgress | None] = ContextVar('extraction_progress', default=None)
_selected: ContextVar[WorkUnit | None] = ContextVar('extraction_progress_unit', default=None)


class ExtractionProgress(OperationProgress):
    """Page state is disk-backed, including terminal records for late events."""

    def __init__(self, callback, path, page_count, selected, *, refinement_enabled=False):
        super().__init__(callback, OperationKind.EXTRACTION)
        self.page_count, self.selected = page_count, selected
        self.refinement_enabled = refinement_enabled
        self.pages = DiskUnits(path, self) if path is not None else None
        self.lock = asyncio.Lock()
        self.report_stage_sync = callback_bridge(asyncio.get_running_loop(), self._stage_pair)
        self.pending_toc: WorkUnit | None = None

    async def _stage_pair(self, pair):
        await self.page(*pair)

    def indexes(self):
        return (index for index in range(1, self.page_count + 1)
                if self.selected is None or index in self.selected)

    @isolated
    async def page(self, index, stage=0, outcome=None, *, refinement_status=None):
        if not 1 <= index <= self.page_count or (self.selected is not None and index not in self.selected):
            return
        assert self.pages is not None
        async with self.lock:
            unit = await IO_DOMAIN.run(self.pages.load, index)
            if unit is None:
                unit = await self.unit(UnitKind.PAGE, page_index=index, max_stage=7)
            if unit.closed:
                return  # Later artifact recovery cannot reopen completed page work.
            if outcome is not None:
                unit.outcome = outcome
            if stage == 7:
                # Only the page worker supplies an affirmative refinement result.
                # Other completion paths must not invent an applied/pass decision.
                status = refinement_status or (RefinementStatus.FALLBACK if self.refinement_enabled
                                               else RefinementStatus.DISABLED)
                await unit.finish(refinement_status=status)
            else:
                await unit.progress(max(unit.stage, stage))
            await IO_DOMAIN.run(self.pages.save, index, unit)

    @isolated
    async def ocr(self, event):
        if event.kind == OCREventKind.IGNORE:
            return
        stage = {OCREventKind.START: 0, OCREventKind.RENDERED: 0}.get(event.kind, 4)
        outcome = {OCREventKind.SKIP: UnitOutcome.REUSED, OCREventKind.FAILED: UnitOutcome.FALLBACK}.get(event.kind)
        await self.page(event.page_index, stage, outcome)

    @isolated
    async def cached_pages(self):
        assert self.pages is not None
        # A complete OCR cache emits no native events. Replay just its scalars.
        for index in self.indexes():
            if await IO_DOMAIN.run(self.pages.load, index) is None:
                await self.page(index, 4, UnitOutcome.REUSED)

    @isolated
    async def finish_pages(self):
        assert self.pages is not None
        if self.pending_toc is not None:
            await self.pending_toc.finish()
            self.pending_toc = None
        for index in self.indexes():
            unit = await IO_DOMAIN.run(self.pages.load, index)
            if unit is None:
                raise RuntimeError('selected page never entered extraction')
            if not unit.closed:
                await self.page(index, 7)
            await IO_DOMAIN.run(self.pages.delete, index)


async def page_stage(index, stage, *, refinement_status=None):
    progress = _active.get()
    if progress is not None:
        await progress.page(index, stage, refinement_status=refinement_status)


def page_stage_sync(index, stage):
    progress = _active.get()
    if progress is not None and not progress.disabled:
        try:
            progress.report_stage_sync((index, stage))
        except FutureCancelledError as error:
            # run_coroutine_threadsafe exposes cancellation as an Exception in
            # the worker. Restore it before OCR error recovery can catch it.
            raise asyncio.CancelledError() from error
        except Exception as error:  # pylint: disable=broad-exception-caught
            progress.disable(error)


async def observe_recovery(context):
    """Accepted recovery changes disposition without completing work early."""
    progress = _active.get()
    unit = _selected.get()
    if unit is not None:
        unit.outcome = UnitOutcome.FALLBACK
    if progress is not None:
        if context.stage == 'chapter_toc' and progress.pending_toc is not None:
            progress.pending_toc.outcome = UnitOutcome.FALLBACK
        # Metadata/TOC source pages are evidence, not degraded page results.
        if unit is None and context.fallback != 'chapter_toc':
            for location in context.affected:
                for index in location.page_indexes:
                    await progress.page(index, outcome=UnitOutcome.FALLBACK)
    from ..transformer.progress import selected_item
    translated = selected_item()
    if translated is not None:
        translated.outcome = UnitOutcome.FALLBACK


@asynccontextmanager
async def analysis_unit(kind, *, reused=False):
    progress = _active.get()
    if progress is None or progress.disabled:
        yield
        return
    unit = progress.pending_toc if kind == UnitKind.TOC else None
    if unit is None:
        unit = await progress.unit(kind, max_stage=1)
    if kind == UnitKind.TOC:
        progress.pending_toc = unit
    token = _selected.set(unit)
    try:
        yield
        if reused and unit.outcome == UnitOutcome.PROCESSED:
            unit.outcome = UnitOutcome.REUSED
        if kind == UnitKind.TOC:
            await unit.progress(1)
            progress.pending_toc = unit
        else:
            await unit.finish()
    finally:
        _selected.reset(token)


def extraction_operation(function):
    """Cover preparation through the outer extractor's successful publication."""
    @wraps(function)
    async def wrapped(self, pdf_path, *args, **kwargs):
        callback = kwargs.get('on_extraction_event')
        if callback is None or _active.get() is not None:
            return await function(self, pdf_path, *args, **kwargs)
        async def count_pages():
            from ..pdf.page_ref import pdf_pages_count
            engine = self._transform
            handler = engine._ocr._get_pdf_handler() if hasattr(engine, '_ocr') else None
            return await IO_DOMAIN.run(pdf_pages_count, pdf_path, handler)
        async with _reporting(callback, count_pages, kwargs.get('page_indexes'),
                              kwargs.get('refinement') is not None):
            return await function(self, pdf_path, *args, **kwargs)
    return wrapped


def active_extraction():
    return _active.get()


def extraction_facade(function):
    """Include asynchronous custom-handler materialization in page lifetimes."""
    import inspect
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        bound = signature.bind_partial(self, *args, **kwargs).arguments
        options = bound.get('options')
        callback = options.on_extraction_event if options is not None else None
        if callback is None or active_extraction() is not None:
            return await function(self, *args, **kwargs)
        assert options is not None
        async def count_pages():
            source = bound['source']
            handler = self._pdf.pdf_handler if self._pdf is not None else None
            if handler is not None and self._engine is None:
                document = await handler.open(source)
                try:
                    return await document.pages_count()
                finally:
                    await document.close()
            from ..pdf.page_ref import pdf_pages_count
            engine = self._pdf_engine()
            native = engine._ocr._get_pdf_handler() if hasattr(engine, '_ocr') else None
            return await IO_DOMAIN.run(pdf_pages_count, source, native)
        async with _reporting(callback, count_pages, options.page_indexes, options.refinement is not None):
            return await function(self, *args, **kwargs)
    return wrapped


@asynccontextmanager
async def _reporting(callback, count_pages, selected, refinement_enabled):
    progress = ExtractionProgress(callback, None, 0, selected, refinement_enabled=refinement_enabled)
    resources = AsyncExitStack()

    async def prepare():
        progress.page_count = await count_pages()
        root = await resources.enter_async_context(temporary_directory('pdf-craft-extraction-progress-'))
        progress.pages = await IO_DOMAIN.run(DiskUnits, root / 'pages.sqlite', progress)
        await progress.start({UnitKind.PAGE: sum(1 for _ in progress.indexes()),
                              UnitKind.TOC: 1, UnitKind.METADATA: 1})

    token = _active.set(progress)
    try:
        await progress.attempt(prepare)
        yield  # Business execution is deliberately outside progress exception handlers.
        await progress.finish_pages()
        await progress.finish()
    finally:
        _active.reset(token)
        await progress.cleanup(resources.aclose)
