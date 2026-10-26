"""Translation scopes and live units; no book-sized progress registry."""
from __future__ import annotations
# pylint: disable=protected-access
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from .._progress import OperationProgress, WorkUnit
from ..progress import OperationKind, UnitOutcome

_active: ContextVar[TranslationProgress | None] = ContextVar('translation_progress', default=None)
_item: ContextVar[WorkUnit | None] = ContextVar('translation_unit', default=None)


class TranslationProgress(OperationProgress):
    """Aggregate counts, with at most two EPUB header units held open."""

    def __init__(self, callback):
        super().__init__(callback, OperationKind.TRANSLATION)
        self.headers = {}


@contextmanager
def progress_context(progress):
    token = _active.set(progress)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def unit_context(unit):
    token = _item.set(unit)
    try:
        yield
    finally:
        _item.reset(token)


def active_progress():
    return _active.get()


def selected_item():
    return _item.get()


def translation_unit(kind, word_count):
    """Keep all nested requests inside one stable adapter-owned unit."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            progress = active_progress()
            if progress is None or not progress.started:
                return await function(*args, **kwargs)
            words = word_count(*args, **kwargs)
            if not words:
                with progress_context(None):
                    return await function(*args, **kwargs)
            unit = await progress.unit(kind, words=words)
            with unit_context(unit):
                result = await function(*args, **kwargs)
            await unit.progress(1)
            outcome = unit.outcome
            if (result is None or result == 'preserved' or
                    (isinstance(result, (list, tuple)) and any(value is None or
                        (isinstance(value, tuple) and value[-1:] == ('preserved',)) for value in result))):
                outcome = UnitOutcome.FALLBACK
            await unit.finish(outcome)
            return result
        return wrapped
    return decorate


def translation_operation(function):
    """Defer operation completion to the outer public output boundary."""
    @wraps(function)
    async def wrapped(*args, **kwargs):
        if active_progress() is not None or kwargs.get('on_translation_event') is None:
            return await function(*args, **kwargs)
        progress = TranslationProgress(kwargs['on_translation_event'])
        with progress_context(progress):
            result = await function(*args, **kwargs)
            await progress.finish()
            return result
    return wrapped


def opaque_batch_units(function):
    """Preserve custom whole-batch contracts without a resident unit plan."""
    @wraps(function)
    async def wrapped(self, chapters, **kwargs):
        progress = active_progress()
        if progress is None or not progress.started or not hasattr(self._translator, 'translate_elements'):
            return await function(self, chapters, **kwargs)
        from ..runtime import temporary_directory, IO_DOMAIN
        from .._progress import DiskUnits
        from ..progress import UnitKind
        # The opaque batch contract itself requires a reusable chapter sequence.
        chapters = list(chapters)
        async with temporary_directory('pdf-craft-opaque-progress-') as root:
            units = await IO_DOMAIN.run(DiskUnits, root / 'units.sqlite', progress)
            for index, (chapter, _) in enumerate(chapters):
                words = self.source_word_count(chapter)
                if words:
                    await IO_DOMAIN.run(units.save, index, await progress.unit(UnitKind.CONTENT, words=words))
            with progress_context(None):
                results = await function(self, chapters, **kwargs)
            for index, _ in enumerate(results):
                unit = await IO_DOMAIN.run(units.load, index)
                if unit is not None:
                    await unit.finish()
                    await IO_DOMAIN.run(units.delete, index)
            return results
    return wrapped
