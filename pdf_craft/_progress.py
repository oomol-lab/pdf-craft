"""Bounded operation accounting shared by both public event adapters."""
import asyncio
import inspect
import logging
from functools import wraps
from concurrent.futures import CancelledError as FutureCancelledError
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager, closing
from types import MappingProxyType

from .progress import ProgressEvent, ProgressEventKind, OperationKind, UnitKind, UnitOutcome
from .runtime import invoke_callback, temporary_directory


def isolated(function):
    """Only for progress-only work: never wrap conversion or content delivery."""
    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def asynchronous(self, *args, **kwargs):
            owner = getattr(self, 'owner', self)
            return await owner.attempt(function, self, *args, **kwargs)
        return asynchronous

    @wraps(function)
    def synchronous(self, *args, **kwargs):
        owner = getattr(self, 'owner', self)
        return owner.attempt_sync(function, self, *args, **kwargs)
    return synchronous


@asynccontextmanager
async def progress_directory(progress, prefix):
    """Optional scratch state, separate from directories holding actual content."""
    resources = AsyncExitStack()
    async def prepare():
        return await resources.enter_async_context(temporary_directory(prefix))
    try:
        yield await progress.attempt(prepare)
    finally:
        await progress.cleanup(resources.aclose)


class OperationProgress:
    """Only aggregates and an ID sequence; no registry of completed units."""

    def __init__(self, callback, operation: OperationKind):
        self.callback = callback
        self.operation = operation
        self.counts = dict.fromkeys(UnitKind, 0)
        self.finished = Counter()
        self.next_id = 0
        self.total_words = None
        self.completed_words = 0
        self.started = False
        self.callback_lock = asyncio.Lock()
        self.disabled = False
        self._diagnosed = False
        self._finished = False

    def diagnose(self, detail):
        """At most one bounded diagnostic, even with broken logging handlers."""
        if self._diagnosed:
            return
        self._diagnosed = True
        try:
            logging.getLogger(__name__).warning('Progress %s: %s', self.operation, detail[:2000])
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # Observability cannot become another delivery prerequisite.

    def disable(self, error, stage='progress'):
        self.disabled = True
        self.diagnose(f'reporting disabled in {stage} ({type(error).__name__})')

    async def attempt(self, function, *args, **kwargs):
        if self.disabled:
            return None
        try:
            return await function(*args, **kwargs)
        except FutureCancelledError as error:
            raise asyncio.CancelledError() from error
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.disable(error, getattr(function, '__qualname__', type(function).__name__))
            return None

    async def cleanup(self, function):
        """Release progress resources even after reporting was disabled."""
        try:
            await function()
        except FutureCancelledError as error:
            raise asyncio.CancelledError() from error
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.disable(error)

    def attempt_sync(self, function, *args, **kwargs):
        if self.disabled:
            return None
        try:
            return function(*args, **kwargs)
        except FutureCancelledError as error:
            raise asyncio.CancelledError() from error
        except Exception as error:  # pylint: disable=broad-exception-caught
            self.disable(error, getattr(function, '__qualname__', type(function).__name__))
            return None

    @isolated
    async def emit(self, kind, **fields):
        async with self.callback_lock:
            if not self.disabled:
                await invoke_callback(self.callback, ProgressEvent(
                    kind, self.operation, completed_words=self.completed_words if self.total_words is not None else None,
                    **fields))

    @isolated
    async def start(self, counts, total_words=None):
        if self.started:
            return
        if any(not isinstance(key, UnitKind) or isinstance(value, bool) or not isinstance(value, int) or value < 0
               for key, value in counts.items()):
            raise ValueError('unit_counts must contain nonnegative integer counts by UnitKind')
        if total_words is not None and (isinstance(total_words, bool) or not isinstance(total_words, int) or total_words < 0):
            raise ValueError('total_words must be a nonnegative integer')
        self.counts.update(counts)
        self.total_words = total_words
        self.started = True
        await self.emit(ProgressEventKind.STARTED,
                        unit_counts=MappingProxyType(dict(self.counts)), total_words=total_words)

    async def unit(self, kind, *, words=None, max_stage=2, page_index=None):
        unit = WorkUnit(self, self.next_id, kind, words, max_stage, page_index)
        self.next_id += 1
        await self.emit(ProgressEventKind.STARTED, **unit.fields(), stage=0,
                        total_words=words, max_stage=max_stage, unit_completed_words=0 if words is not None else None)
        return unit

    @isolated
    async def finish(self):
        if not self.started or self._finished:
            return
        self._finished = True
        if (any(self.finished[kind] != count for kind, count in self.counts.items()) or
                (self.total_words is not None and self.completed_words != self.total_words)):
            units = {kind.name: (count, self.finished[kind], self.finished[kind] - count)
                     for kind, count in self.counts.items()}
            delta = None if self.total_words is None else self.completed_words - self.total_words
            self.diagnose(f'accounting mismatch: units(expected, actual, delta)={units}; '
                          f'words(expected, actual, delta)=({self.total_words}, {self.completed_words}, {delta})')
        await self.emit(ProgressEventKind.COMPLETED)


class WorkUnit:
    """Live scalar state, owned by the adapter and released after completion."""

    def __init__(self, owner, identifier, kind, words, max_stage, page_index=None):
        self.owner, self.id, self.kind = owner, identifier, kind
        self.total_words, self.max_stage, self.page_index = words, max_stage, page_index
        self.words = 0
        self.stage = 0
        self.outcome = UnitOutcome.PROCESSED
        self.closed = False
        self._lock = asyncio.Lock()
        self._failure: BaseException | None = None

    def fields(self):
        return dict(unit_id=self.id, unit_kind=self.kind, page_index=self.page_index)

    @isolated
    async def progress(self, stage=None, words=0):
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            try:
                await self._progress(stage, words)
            except BaseException as error:
                self._failure = error
                raise

    async def _progress(self, stage=None, words=0):
        if self.closed:
            raise RuntimeError('cannot advance a completed unit')
        if stage is not None:
            if not self.stage <= stage <= self.max_stage:
                raise RuntimeError('unit stage must advance within its declared range')
            self.stage = stage
        if words < 0 or (self.total_words is not None and self.words + words > self.total_words):
            raise RuntimeError('unit word progress exceeded its source total')
        self.words += words
        self.owner.completed_words += words
        await self.owner.emit(ProgressEventKind.PROGRESS, **self.fields(), stage=self.stage,
                              unit_completed_words=self.words if self.total_words is not None else None)

    @isolated
    async def finish(self, outcome=None, *, refinement_status=None):
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            try:
                await self._finish(outcome, refinement_status)
            except BaseException as error:
                self._failure = error
                raise

    async def _finish(self, outcome, refinement_status):
        if self.closed:
            return
        if outcome is not None:
            self.outcome = outcome
        await self._progress(self.max_stage, (self.total_words - self.words) if self.total_words is not None else 0)
        self.closed = True
        self.owner.finished[self.kind] += 1
        await self.owner.emit(ProgressEventKind.COMPLETED, **self.fields(), stage=self.stage,
                              unit_completed_words=self.words if self.total_words is not None else None,
                              outcome=self.outcome, refinement_status=refinement_status)


class DiskUnits:
    """Pending units across whole-book barriers live on disk, not in a registry."""

    def __init__(self, path, owner):
        self.path, self.owner = path, owner
        self._create()

    @isolated
    def _create(self):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('CREATE TABLE units (key TEXT PRIMARY KEY, state BLOB NOT NULL)')

    @isolated
    def save(self, key, unit):
        import pickle
        import sqlite3
        state = (unit.id, unit.kind, unit.total_words, unit.max_stage, unit.page_index,
                 unit.words, unit.stage, unit.outcome, unit.closed)
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('INSERT OR REPLACE INTO units VALUES (?, ?)', (str(key), pickle.dumps(state)))

    @isolated
    def load(self, key):
        import pickle
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                row = db.execute('SELECT state FROM units WHERE key=?', (str(key),)).fetchone()
        if row is None:
            return None
        identifier, kind, words, maximum, page, done, stage, outcome, closed = pickle.loads(row[0])
        unit = WorkUnit(self.owner, identifier, kind, words, maximum, page)
        unit.words, unit.stage, unit.outcome, unit.closed = done, stage, outcome, closed
        return unit

    @isolated
    def delete(self, key):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('DELETE FROM units WHERE key=?', (str(key),))
