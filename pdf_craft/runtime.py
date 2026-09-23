"""Async execution primitives for blocking third-party boundaries.

pdf-craft deliberately keeps synchronous libraries behind coarse execution
domains.  The event loop owns orchestration and callbacks; workers own every
object created by pypdf, Pillow, doc-page-extractor, Qt, and epub-generator.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import pickle
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
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


class ProcessExecutionDomain:
    """Run process-global native runtimes in a fresh isolated main thread."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, function: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
        if not function.__module__.startswith("pdf_craft.") or "<locals>" in function.__qualname__:
            raise TypeError("process-domain functions must be module-level pdf_craft callables")
        request = pickle.dumps(
            (function.__module__, function.__qualname__, args, kwargs),
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pdf_craft._process_worker", self.name,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate(request)
        except asyncio.CancelledError:
            if process.returncode is None:
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
                f"{self.name} process exited with code {process.returncode}: {detail}"
            )
        status, value, remote_traceback = pickle.loads(stdout)
        if status == "error":
            if isinstance(value, BaseException):
                value.add_note(f"Remote {self.name} traceback:\n{remote_traceback}")
                raise value
            raise RuntimeError(f"{self.name} process failed: {value}\n{remote_traceback}")
        return cast(R, value)


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
# QGuiApplication must live on the main thread of its own process on macOS and
# has process-global state elsewhere. A fresh subprocess per operation avoids
# inheriting a host application's Qt state and is portable to spawn-only hosts.
QT_DOMAIN = ProcessExecutionDomain("qt")
# Matplotlib/TeX is serialized on a stable worker and never touches Qt.
TEX_DOMAIN = ExecutionDomain("tex", 1)


@asynccontextmanager
async def temporary_directory(prefix: str) -> AsyncIterator[Path]:
    """Create and recursively clean a temporary directory on the I/O domain."""
    temporary = await IO_DOMAIN.run(TemporaryDirectory, prefix=prefix)
    try:
        yield Path(temporary.name)
    finally:
        await IO_DOMAIN.run(temporary.cleanup)


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
        # Shield the executor future: cancelling the caller must not mark the
        # asyncio wrapper complete while its thread still owns workspace files.
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        bridge.cancel()
        # Keep all resources owned by the surrounding async context alive until
        # the cooperative worker has observed cancellation and fully unwound.
        # Repeated cancellation requests still cannot detach the live worker.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                bridge.cancel()
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


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
