"""Operation-local translation accounting; never mutates shared model clients."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from ..runtime import invoke_callback
from .events import TranslationEvent, TranslationEventKind, TranslationItemKind

_active: ContextVar[TranslationProgress | None] = ContextVar('translation_progress', default=None)
_item: ContextVar[tuple[TranslationItemKind, str | int | None] | None] = ContextVar('translation_progress_item', default=None)


class TranslationProgress:
    """Scalar totals per public item, with serialized cumulative notifications."""

    def __init__(self, callback, totals, *, completed=0, item_events=True):
        self.callback = callback
        self.item_events = item_events
        self.totals = dict(totals)
        self.completed = completed
        self.total: int | None = completed + sum(self.totals.values())
        self.chapter_count = sum(key[0] == TranslationItemKind.CHAPTER for key in self.totals)
        self.has_toc = any(key[0] == TranslationItemKind.TOC for key in self.totals)
        self.has_metadata = any(key[0] == TranslationItemKind.METADATA for key in self.totals)
        self.done = dict.fromkeys(self.totals, 0)
        self.started = set()
        self.finished = set()
        self.lock = asyncio.Lock()

    async def emit(self, kind, key=None):
        event = TranslationEvent(
            kind, chapter_count=self.chapter_count,
            has_toc=self.has_toc, has_metadata=self.has_metadata,
            item_kind=key[0] if key else None, item_id=key[1] if key else None,
            item_completed_words=self.done[key] if key else None,
            item_total_words=self.totals[key] if key else None,
            completed_words=self.completed, total_words=self.total)
        try:
            await invoke_callback(self.callback, event)
        except Exception as error:
            from ..policies import ErrorCallbackError
            raise ErrorCallbackError('translation progress callback failed') from error

    async def start_item(self, key):
        if key not in self.totals:
            return
        async with self.lock:
            if key not in self.started:
                self.started.add(key)
                if self.item_events:
                    await self.emit(TranslationEventKind.ITEM_START, key)

    async def advance(self, key, words):
        if key not in self.totals:
            return
        async with self.lock:
            if key not in self.started:
                self.started.add(key)
                if self.item_events:
                    await self.emit(TranslationEventKind.ITEM_START, key)
            if words < 0 or words > self.totals[key] - self.done[key]:
                raise RuntimeError('translation word accounting exceeded its source total')
            self.done[key] += words
            self.completed += words
            await self.emit(TranslationEventKind.PROGRESS, key)

    async def finish_item(self, key):
        if key not in self.totals or key in self.finished:
            return
        if self.done[key] < self.totals[key] or key not in self.started:
            await self.advance(key, self.totals[key] - self.done[key])
        async with self.lock:
            if key not in self.finished:
                self.finished.add(key)
                if self.item_events:
                    await self.emit(TranslationEventKind.ITEM_COMPLETE, key)

    async def finish(self):
        # Only adapters that reached their actual EOF/finalization may close
        # items. Closing a partially consumed stream must not fabricate work.
        for key, words in self.totals.items():
            if not words:
                await self.finish_item(key)
        if any(key not in self.finished for key in self.totals):
            return
        if self.total is None:
            self.total = self.completed
        await self.emit(TranslationEventKind.COMPLETE)


@contextmanager
def progress_context(progress):
    token = _active.set(progress)
    try:
        yield
    finally:
        _active.reset(token)


def active_progress():
    return _active.get()


def selected_item():
    return _item.get()


def translation_item(kind, identifier, *, complete=False):
    """Bind adapter work, including its nested fill/asset calls, to one item."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            key = (kind, identifier(*args, **kwargs))
            token = _item.set(key)
            try:
                progress = active_progress()
                if progress is not None:
                    await progress.start_item(key)
                result = await function(*args, **kwargs)
                if complete and progress is not None:
                    await progress.finish_item(key)
                return result
            finally:
                _item.reset(token)
        return wrapped
    return decorate


async def finish_chapters(items):
    """Close chapter items on ordered consumer boundaries, not request order."""
    from contextlib import aclosing
    previous = None
    progress = active_progress()
    async with aclosing(items):
        async for item in items:
            key = (TranslationItemKind.CHAPTER, item.chapter_id if item.chapter_id is not None else 'head')
            if previous is not None and previous != key and progress is not None:
                await progress.finish_item(previous)
            previous = key
            yield item
    if previous is not None and progress is not None:
        await progress.finish_item(previous)
