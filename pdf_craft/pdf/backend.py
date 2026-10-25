"""Task-local Poppler executable configuration, without input validation."""
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
import inspect
from pathlib import Path
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])
_poppler_path: ContextVar[Path | None] = ContextVar("pdf_poppler_path", default=None)


def current_poppler_path() -> Path | None:
    """Tool configuration shared by nested operations and default readers."""
    return _poppler_path.get()


def poppler_path_for(provider: object) -> Path | None:
    """Resolve configured adapters without extending custom handler contracts."""
    from .handler import DefaultPDFHandler
    if isinstance(provider, DefaultPDFHandler):
        return provider.poppler_path
    # Do not probe arbitrary attributes on caller-owned proxies or handlers.
    resolve = getattr(type(provider), "_backend_poppler_path", None)
    return resolve(provider) if resolve is not None else None


def pdf_backend_scope(function: F) -> F:
    """Share only Poppler tool configuration; never validate the source."""
    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args, **kwargs):
        bound = dict(signature.bind_partial(*args, **kwargs).arguments)
        values = {**bound, **bound.get("kwargs", {})}
        tools = poppler_path_for(values.get("self"))
        tool_token = _poppler_path.set(tools) if tools is not None else None
        try:
            return await function(*args, **kwargs)
        finally:
            if tool_token is not None:
                _poppler_path.reset(tool_token)
    return cast(F, invoke)
