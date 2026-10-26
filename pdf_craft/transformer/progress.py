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
_assets: ContextVar[dict[str, WorkUnit] | None] = ContextVar('translation_assets', default=None)


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



@contextmanager
def asset_unit_context(units):
    token = _assets.set(units)
    try:
        yield
    finally:
        _assets.reset(token)


def selected_assets():
    return _assets.get()
