"""Keep caller-owned fill recovery callbacks outside native worker processes."""

import asyncio
import inspect
import pickle
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from multiprocessing.connection import Client, Listener
from threading import Lock, Thread
from typing import Any, AsyncIterator, cast

from ...policies import ErrorPolicyInput, FailureContext, ErrorCallbackError
from ..._policy import OMITTED, current, decide_context
from dataclasses import replace
from ...runtime import worker_context


@dataclass(frozen=True)
class _RemoteErrorPolicyChecker:
    """Pickle-safe proxy for a caller-owned fill recovery predicate."""

    address: Any
    authkey: bytes

    def __call__(self, error: FailureContext) -> bool:
        connection = Client(self.address, authkey=self.authkey)
        try:
            connection.send(error)
            status, value = connection.recv()
        finally:
            connection.close()
        if status == "error":
            if isinstance(value, BaseException):
                raise value
            raise RuntimeError(f"error_policy callback failed: {value}")
        return bool(value)


class _ErrorPolicyCheckerServer:
    """Serve a local predicate to an isolated process without pickling it."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        checker: Callable[[FailureContext], bool],
    ) -> None:
        self._state_lock = Lock()
        self._closing = False
        self.callback_error = None
        self._pending = None
        self._loop = loop
        self._checker = checker
        self._authkey = secrets.token_bytes(32)
        self._listener = Listener(("127.0.0.1", 0), authkey=self._authkey)
        self._thread = Thread(
            target=self._serve,
            name="pdf-craft-ignore-errors",
            daemon=True,
        )
        self._thread.start()

    def proxy(self) -> _RemoteErrorPolicyChecker:
        return _RemoteErrorPolicyChecker(self._listener.address, self._authkey)

    def _serve(self) -> None:
        while True:
            try:
                connection = self._listener.accept()
            except OSError:
                return
            try:
                error = connection.recv()
                if error is None:
                    return
                if not isinstance(error, FailureContext):
                    raise TypeError("worker must send FailureContext")
                future = asyncio.run_coroutine_threadsafe(
                    _invoke_error_policy_checker(self._checker, error),
                    self._loop,
                )
                with self._state_lock:
                    self._pending = future
                    if self._closing:
                        future.cancel()
                try:
                    response = ("ok", future.result())
                except BaseException as callback_error:  # pylint: disable=broad-exception-caught
                    if isinstance(callback_error, ErrorCallbackError):
                        self.callback_error = callback_error
                    response = ("error", _pickle_safe_exception(callback_error))
                finally:
                    with self._state_lock:
                        self._pending = None
                connection.send(response)
            except (EOFError, OSError):
                pass
            finally:
                connection.close()

    def close(self) -> None:
        with self._state_lock:
            self._closing = True
            if self._pending is not None:
                self._pending.cancel()
        try:
            connection = Client(self._listener.address, authkey=self._authkey)
            try:
                connection.send(None)
            finally:
                connection.close()
        except (ConnectionError, OSError):
            pass
        self._thread.join(timeout=5)
        self._listener.close()
        if self._thread.is_alive():
            raise RuntimeError("error_policy callback server did not stop")


async def _invoke_error_policy_checker(
    checker: Callable[[FailureContext], bool],
    error: FailureContext,
) -> bool:
    result = checker(error)
    if inspect.isawaitable(result):
        result = await cast(Awaitable[bool], result)
    return bool(result)


def _pickle_safe_exception(error: BaseException) -> BaseException:
    try:
        pickle.dumps(error, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # pylint: disable=broad-exception-caught
        return RuntimeError(f"{type(error).__module__}.{type(error).__qualname__}: {error}")
    return error


@asynccontextmanager
async def _process_error_policy_checker(
    checker: ErrorPolicyInput,
) -> AsyncIterator[ErrorPolicyInput]:
    if isinstance(checker, _RemoteErrorPolicyChecker):
        yield checker
        return
    execution = current()
    selected = execution.errors if checker is OMITTED else checker
    if selected is None or isinstance(selected, bool):
        yield bool(selected)
        return

    async def decide(context):
        context = replace(context, task_id=execution.task_id, operation=execution.operation,
                          targets=tuple(replace(location, source=location.source or execution.source)
                                        for location in context.targets),
                          affected=tuple(replace(location, source=location.source or execution.source)
                                         for location in context.affected))
        return await decide_context(context, selected)

    async with worker_context(_checker_server(asyncio.get_running_loop(), decide)) as proxy:
        yield proxy


@contextmanager
def _checker_server(loop, checker):
    server = _ErrorPolicyCheckerServer(loop, checker)
    try:
        yield server.proxy()
    finally:
        server.close()
        if server.callback_error is not None:
            raise server.callback_error
