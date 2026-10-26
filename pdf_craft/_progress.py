"""Bounded operation accounting shared by both public event adapters."""
import asyncio
from collections import Counter
from contextlib import closing
from types import MappingProxyType

from .progress import ProgressEvent, ProgressEventKind, OperationKind, UnitKind, UnitOutcome
from .runtime import invoke_callback


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

    async def emit(self, kind, **fields):
        async with self.callback_lock:
            try:
                await invoke_callback(self.callback, ProgressEvent(
                    kind, self.operation, completed_words=self.completed_words if self.total_words is not None else None,
                    **fields))
            except Exception as error:
                from .policies import ErrorCallbackError
                raise ErrorCallbackError('progress callback failed') from error

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

    async def finish(self):
        if not self.started:
            return
        if any(self.finished[kind] != count for kind, count in self.counts.items()):
            raise RuntimeError('completed unit counts do not match the operation start')
        if self.total_words is not None and self.completed_words != self.total_words:
            raise RuntimeError('completed words do not match the operation start')
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

    def fields(self):
        return dict(unit_id=self.id, unit_kind=self.kind, page_index=self.page_index)

    async def progress(self, stage=None, words=0):
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

    async def finish(self, outcome=None):
        if self.closed:
            return
        if outcome is not None:
            self.outcome = outcome
        await self.progress(self.max_stage, (self.total_words - self.words) if self.total_words is not None else 0)
        self.closed = True
        self.owner.finished[self.kind] += 1
        await self.owner.emit(ProgressEventKind.COMPLETED, **self.fields(), stage=self.stage,
                              unit_completed_words=self.words if self.total_words is not None else None,
                              outcome=self.outcome)


class DiskUnits:
    """Pending units across whole-book barriers live on disk, not in a registry."""

    def __init__(self, path, owner):
        import sqlite3
        self.path, self.owner = path, owner
        with closing(sqlite3.connect(path)) as db:
            with db:
                db.execute('CREATE TABLE units (key TEXT PRIMARY KEY, state BLOB NOT NULL)')

    def save(self, key, unit):
        import pickle
        import sqlite3
        state = (unit.id, unit.kind, unit.total_words, unit.max_stage, unit.page_index,
                 unit.words, unit.stage, unit.outcome, unit.closed)
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('INSERT OR REPLACE INTO units VALUES (?, ?)', (str(key), pickle.dumps(state)))

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

    def delete(self, key):
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute('DELETE FROM units WHERE key=?', (str(key),))
