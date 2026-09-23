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
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from threading import Event, Lock
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
        future = loop.run_in_executor(
            self._executor, partial(function, *args, **kwargs),
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Python cannot stop a running executor thread. Keep ownership of
            # every path, handle, and mutable object used by that worker until
            # it has really unwound, even under repeated cancellation.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()
            raise


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
        registry_path = await asyncio.to_thread(_create_process_group_registry)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pdf_craft._process_worker", self.name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "PDF_CRAFT_PROCESS_GROUP_REGISTRY": str(registry_path),
                },
                **_process_group_options(),
            )
            try:
                stdout, stderr = await process.communicate(request)
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    _terminate_registered_process_groups, registry_path,
                )
                await _terminate_async_process(process, process_tree=True)
                await asyncio.to_thread(
                    _terminate_registered_process_groups, registry_path,
                )
                raise
        finally:
            if process is not None and process.returncode not in (None, 0):
                await asyncio.to_thread(
                    _terminate_registered_process_groups, registry_path,
                )
            await asyncio.to_thread(registry_path.unlink, missing_ok=True)
        assert process is not None
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


class AtomicCancellationBridge:
    """Serialize cancellation against one irreversible completion action."""

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._committed = False

    def check(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> bool:
        """Return whether cancellation won before the completion boundary."""
        with self._lock:
            if self._committed:
                return False
            self._event.set()
            return True

    def commit(self, action: Callable[[], object]) -> bool:
        """Run an irreversible action only if cancellation has not won."""
        with self._lock:
            if self._event.is_set():
                return False
            action()
            self._committed = True
            return True

    @property
    def committed(self) -> bool:
        with self._lock:
            return self._committed


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


async def run_atomic_cancellable(
    domain: ExecutionDomain,
    function: Callable[[AtomicCancellationBridge], R],
) -> R:
    """Run blocking work whose final mutation is an atomic completion boundary.

    Cancellation before ``bridge.commit`` prevents that mutation and waits for
    the worker to unwind. Cancellation racing after commit is suppressed: the
    operation already completed and its result is returned.
    """
    bridge = AtomicCancellationBridge()
    task = asyncio.create_task(domain.run(function, bridge))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancellation_won = bridge.cancel()
        cancellation_count = 1
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_count += 1
                cancellation_won = bridge.cancel() or cancellation_won
            except Exception:
                break
        if cancellation_won:
            if task.done() and not task.cancelled():
                task.exception()
            raise
        if not bridge.committed:
            raise
        current = asyncio.current_task()
        if current is not None:
            for _ in range(cancellation_count):
                current.uncancel()
        return task.result()


async def run_subprocess(
    *command: str,
    input_data: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Run a system command without blocking the loop and reap it on cancel."""
    platform_command = _platform_command(command)
    process = await asyncio.create_subprocess_exec(
        *platform_command,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **_process_group_options(),
    )
    registry_path = _process_group_registry_path()
    if registry_path is not None:
        _register_process_group(registry_path, process.pid)
    try:
        stdout, stderr = await process.communicate(input_data)
    except asyncio.CancelledError:
        await _terminate_async_process(process, process_tree=True)
        raise
    finally:
        if process.returncode is not None and os.name != "nt":
            _signal_process(process.pid, signal.SIGKILL, process_tree=True)
        if registry_path is not None:
            _unregister_process_group(registry_path, process.pid)
    if process.returncode:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Command {command[0]!r} failed with exit code {process.returncode}: {detail}"
        )
    return stdout, stderr


def run_subprocess_sync(
    *command: str,
    aborted: Callable[[], bool] | None = None,
) -> tuple[bytes, bytes]:
    """Run a command in a worker thread with cooperative tree cancellation."""
    process = cast(subprocess.Popen[bytes], subprocess.Popen(  # pylint: disable=consider-using-with
        _platform_command(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        **_process_group_options(),
    ))
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired as error:
                if aborted is not None and aborted():
                    _terminate_sync_process(process, process_tree=True)
                    raise asyncio.CancelledError from error
    finally:
        if process.poll() is None:
            _terminate_sync_process(process, process_tree=True)
        elif os.name != "nt":
            _signal_process(process.pid, signal.SIGKILL, process_tree=True)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    if process.returncode:
        detail = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Command {command[0]!r} failed with exit code {process.returncode}: {detail}"
        )
    return stdout, stderr


def _process_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _platform_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Give Windows commands a Job Object that outlives their descendants."""
    if os.name != "nt":
        return command
    return (sys.executable, "-m", "pdf_craft._windows_process_wrapper", *command)


def _create_process_group_registry() -> Path:
    descriptor, raw_path = mkstemp(prefix="pdf-craft-process-groups-")
    os.close(descriptor)
    return Path(raw_path)


def _process_group_registry_path() -> Path | None:
    raw_path = os.environ.get("PDF_CRAFT_PROCESS_GROUP_REGISTRY")
    return Path(raw_path) if raw_path else None


def _register_process_group(path: Path, pid: int) -> None:
    with path.open("a", encoding="ascii") as registry:
        registry.write(f"{pid}\n")


def _unregister_process_group(path: Path, pid: int) -> None:
    try:
        registered = {
            int(value) for value in path.read_text(encoding="ascii").splitlines()
            if value.isdigit() and int(value) != pid
        }
        path.write_text(
            "".join(f"{value}\n" for value in sorted(registered)),
            encoding="ascii",
        )
    except FileNotFoundError:
        pass


def _terminate_registered_process_groups(path: Path) -> None:
    try:
        pids = {
            int(value) for value in path.read_text(encoding="ascii").splitlines()
            if value.isdigit()
        }
    except FileNotFoundError:
        return
    for pid in pids:
        if os.name == "nt":
            subprocess.run(
                ("taskkill", "/PID", str(pid), "/T", "/F"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            _signal_process(pid, signal.SIGKILL, process_tree=True)


async def _terminate_async_process(
    process: asyncio.subprocess.Process,
    *,
    process_tree: bool,
) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt" and process_tree:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        _signal_process(process.pid, signal.SIGTERM, process_tree=process_tree)
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        if os.name == "nt":
            process.kill()
        else:
            _signal_process(process.pid, signal.SIGKILL, process_tree=process_tree)
        await process.wait()
    finally:
        if process_tree and os.name != "nt":
            # The group leader can exit before a descendant that ignored
            # SIGTERM. The process group remains addressable until its final
            # member exits, so force-reap any survivor after the worker ends.
            _signal_process(process.pid, signal.SIGKILL, process_tree=True)


def _terminate_sync_process(
    process: subprocess.Popen[bytes],
    *,
    process_tree: bool,
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt" and process_tree:
        subprocess.run(
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        _signal_process(process.pid, signal.SIGTERM, process_tree=process_tree)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            _signal_process(process.pid, signal.SIGKILL, process_tree=process_tree)
        process.wait()
    finally:
        if process_tree and os.name != "nt":
            _signal_process(process.pid, signal.SIGKILL, process_tree=True)


def _signal_process(pid: int, sig: signal.Signals, *, process_tree: bool) -> None:
    try:
        if process_tree and os.name != "nt":
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        pass
