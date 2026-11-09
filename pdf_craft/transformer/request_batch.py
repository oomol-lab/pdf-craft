"""One operation's bounded model request capacity; grouping belongs to the mapper."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
from collections.abc import Callable, Awaitable
from typing import TypeVar, cast

MAX_BATCH_UNITS = 64
_REQUEST_WINDOW: ContextVar[asyncio.Semaphore | None] = ContextVar('translation_request_window', default=None)
_F = TypeVar('_F', bound=Callable[..., Awaitable[object]])

@contextmanager
def request_window(window: int):
    """One operation's request slots, inherited across content adapters."""
    if window < 1:
        raise ValueError('window must be at least 1')
    if _REQUEST_WINDOW.get() is not None:
        yield
        return
    token = _REQUEST_WINDOW.set(asyncio.Semaphore(window))
    try:
        yield
    finally:
        _REQUEST_WINDOW.reset(token)


def with_request_window(function: _F) -> _F:
    """Keep a method's window as transport capacity, outside ready collection."""
    signature = inspect.signature(function)
    @wraps(function)
    async def invoke(*args, **kwargs):
        window = signature.bind_partial(*args, **kwargs).arguments.get('window', 1)
        with request_window(window):
            return await function(*args, **kwargs)
    return cast(_F, invoke)


async def request(context, messages, **options):
    capacity = _REQUEST_WINDOW.get()
    if capacity is None:
        return await context.request(messages, **options)
    async with capacity:
        return await context.request(messages, **options)
