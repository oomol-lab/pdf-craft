"""Async execution primitives for blocking third-party boundaries.

pdf-craft deliberately keeps synchronous libraries behind coarse execution
domains.  The event loop owns orchestration and callbacks; workers own every
object created by pypdf, Pillow, doc-page-extractor, Qt, and epub-generator.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Event
from typing import Any, ParamSpec, TypeVar, cast


P = ParamSpec("P")
R = TypeVar("R")
AsyncCallback = Callable[[Any], object | Awaitable[object]]


class ExecutionDomain:
    """A named, bounded executor for one family of blocking operations."""

    def __init__(self, name: str, max_workers: int) -> None:
        self.name = name
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"pdf-craft-{name}",
        )

    async def run(self, function: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(function, *args, **kwargs))


# Short filesystem/archive tasks must not queue behind minute-long OCR calls.
IO_DOMAIN = ExecutionDomain("io", max(4, min(32, (os.cpu_count() or 1) + 4)))
# Stateful archive adapters keep their open handles on one stable worker.
ARCHIVE_DOMAIN = ExecutionDomain("archive", 1)
# doc-page-extractor owns synchronous generators, requests sessions and model
# instances. Keep the full call on one worker and bound resource consumption.
# A single conservative slot prevents a model/GPU session from being re-entered.
# Applications needing parallel OCR should own multiple isolated SDK processes.
OCR_DOMAIN = ExecutionDomain("ocr", 1)
TRANSLATION_DOMAIN = ExecutionDomain("translation", max(2, min(8, os.cpu_count() or 1)))
# QGuiApplication and Matplotlib/TeX both have process-global state. A stable
# single worker preserves object/thread affinity instead of using to_thread's
# arbitrary default-executor workers.
QT_DOMAIN = ExecutionDomain("qt", 1)
TEX_DOMAIN = ExecutionDomain("tex", 1)


def run_sync(awaitable: Awaitable[R]) -> R:
    """Run an async SDK operation from the synchronous compatibility facade."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        async def wait() -> R:
            return await awaitable
        return asyncio.run(wait())
    if inspect.iscoroutine(awaitable):
        awaitable.close()
    raise RuntimeError(
        "The synchronous pdf-craft API cannot run inside an active event loop; "
        "use AsyncPDFCraft (or the component's async method) and await it instead."
    )


def require_sync_context() -> None:
    """Reject synchronous SDK work on an already-running event-loop thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "The synchronous pdf-craft API cannot run inside an active event loop; "
        "use AsyncPDFCraft (or the component's async method) and await it instead."
    )


async def invoke_callback(callback: Callable[[R], object] | None, value: R) -> None:
    """Invoke a user callback on the event-loop thread and await it when needed."""
    if callback is None:
        return
    result = callback(value)
    if inspect.isawaitable(result):
        await cast(Awaitable[object], result)


def callback_bridge(
    loop: asyncio.AbstractEventLoop,
    callback: Callable[[R], object] | None,
) -> Callable[[R], None]:
    """Turn a loop-owned callback into a synchronous worker callback."""
    if callback is None:
        return lambda _value: None

    def dispatch(value: R) -> None:
        future = asyncio.run_coroutine_threadsafe(invoke_callback(callback, value), loop)
        future.result()

    return dispatch


class CancellationBridge:
    """Expose asyncio cancellation to cooperative synchronous dependencies."""

    def __init__(self, original: Callable[[], bool] | None = None) -> None:
        self._event = Event()
        self._original = original

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> bool:
        return self._event.is_set() or (self._original() if self._original else False)


async def run_cancellable(
    domain: ExecutionDomain,
    function: Callable[[Callable[[], bool]], R],
    *,
    original_aborted: Callable[[], bool] | None = None,
) -> R:
    """Run a cooperative blocking stage and signal it if its task is cancelled."""
    bridge = CancellationBridge(original_aborted)
    task = asyncio.create_task(domain.run(function, bridge.check))
    try:
        return await task
    except asyncio.CancelledError:
        bridge.cancel()
        # Do not claim that cancelling Future stopped its underlying thread.
        # The dependency observes ``bridge.check`` at its next safe boundary.
        raise
    finally:
        if task.done() and not task.cancelled():
            task.exception()


async def run_subprocess(
    *command: str,
    input_data: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Run a system command without blocking the loop and reap it on cancel."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate(input_data)
    except asyncio.CancelledError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        raise
    if process.returncode:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Command {command[0]!r} failed with exit code {process.returncode}: {detail}"
        )
    return stdout, stderr
