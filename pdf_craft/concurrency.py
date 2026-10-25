"""Shared asynchronous capacity control for remote operations."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from functools import wraps
from typing import Any, Generic, Protocol, TypeVar, cast


T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])


class OperationError(Exception):
    """A recoverable failure of one remote operation."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.attempts: int | None = None
        if cause is not None:
            self.__cause__ = cause

    def for_operation(self, operation_id: int) -> "OperationError":
        if self.operation_id is None:
            self.operation_id = operation_id
        return self


class RateLimitedError(OperationError):
    """The provider temporarily refused an operation because of rate limits."""

    def __init__(
        self,
        message: str = "The provider rate limit was exceeded.",
        *,
        operation_id: int | None = None,
        retry_after: float | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, operation_id=operation_id, cause=cause)
        self.retry_after = retry_after


class NonContinuableError(OperationError):
    """A provider failure that permanently closes one executor channel."""


class _FatalErrorState:
    """First observed fatal error for a task group, inherited by child tasks.

    Request pools keep their own terminal state; this state additionally spans
    pools within one business operation. It never converts an ordinary external
    cancellation into a provider failure without an observed terminal cause.
    """

    def __init__(self) -> None:
        self.parent = _fatal_state.get()
        self.error: NonContinuableError | None = None
        self.links: tuple[BaseException | None, BaseException | None, bool] | None = None

    def record(self, error: NonContinuableError) -> None:
        if self.error is None:
            self.error = error
            self.links = (error.__cause__, error.__context__, error.__suppress_context__)
        if self.parent is not None:
            self.parent.record(error)

    def raise_fatal(self) -> None:
        assert self.error is not None and self.links is not None
        # A raise performed during generator finalization or another exception's
        # cleanup would otherwise attach that secondary exception as context.
        try:
            raise self.error
        finally:
            self.error.__cause__, self.error.__context__, self.error.__suppress_context__ = self.links

    async def run(self, operation: Awaitable[T]) -> T:
        token = _fatal_state.set(self)
        caught: BaseException | None = None
        result: Any = None
        try:
            result = await operation
        except BaseException as error:
            caught = error
            fatal = _fatal_from_error(error)
            if fatal is not None:
                self.record(fatal)
        finally:
            _fatal_state.reset(token)
        # Raise outside the except block. Cancellation is a consequence, never
        # a new cause/context of the original provider error.
        if self.error is not None:
            self.raise_fatal()
        if caught is not None:
            raise caught
        return cast(T, result)


_fatal_state: ContextVar[_FatalErrorState | None] = ContextVar(
    "pdf_craft_fatal_state", default=None,
)


def _fatal_from_error(error: BaseException) -> NonContinuableError | None:
    if isinstance(error, NonContinuableError):
        return error
    if isinstance(error, asyncio.CancelledError):
        return next((arg for arg in error.args if isinstance(arg, NonContinuableError)), None)
    return None


def _record_fatal(error: NonContinuableError) -> None:
    state = _fatal_state.get()
    if state is not None:
        state.record(error)


def fatal_error_boundary(function: F) -> F:
    """Keep a group's first fatal cause through sibling cancellation/cleanup.

    For streams the context is active only while advancing/closing the iterator,
    so it cannot leak into unrelated work performed by the stream's consumer.
    Child task contexts still share the group's mutable state.
    """
    if inspect.isasyncgenfunction(function):
        @wraps(function)
        async def stream(*args, **kwargs):
            state = _FatalErrorState()
            iterator = function(*args, **kwargs)
            try:
                while True:
                    try:
                        value = await state.run(anext(iterator))
                    except StopAsyncIteration:
                        return
                    yield value
            finally:
                await state.run(iterator.aclose())
        return cast(F, stream)

    @wraps(function)
    async def invoke(*args, **kwargs):
        return await _FatalErrorState().run(function(*args, **kwargs))
    return cast(F, invoke)


class ExecutionOutcome(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecutionReport:
    elapsed_seconds: float
    outcome: ExecutionOutcome
    error: BaseException | None = None


class CapacityLease(Protocol):
    async def release(self, report: ExecutionReport) -> None: ...


class CapacityProvider(Protocol):
    async def acquire(self) -> CapacityLease: ...

    async def close(self, error: NonContinuableError) -> None: ...


async def _release_without_abandoning(
    lease: CapacityLease,
    report: ExecutionReport,
) -> asyncio.CancelledError | None:
    """Finish an asynchronous release despite repeated caller cancellation."""
    release_task = asyncio.create_task(lease.release(report))
    interrupted: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(release_task)
            return interrupted
        except asyncio.CancelledError as error:
            if release_task.done():
                # If release itself was cancelled, result() propagates that
                # failure.  Otherwise the caller cancellation raced with a
                # release that had already completed successfully.
                release_task.result()
                return error
            interrupted = error


async def _settle_tasks_without_abandoning(
    tasks: list[asyncio.Task[object]],
) -> asyncio.CancelledError | None:
    """Wait for task teardown even if the owning consumer is cancelled again."""
    if not tasks:
        return None
    settling = asyncio.gather(*tasks, return_exceptions=True)
    interrupted: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(settling)
            return interrupted
        except asyncio.CancelledError as error:
            if settling.done():
                settling.result()
                return error
            interrupted = error


async def settle_tasks(tasks: Iterable[asyncio.Task[Any]]) -> None:
    """Finish owned task cleanup without forwarding repeated cancellation.

    Once a fatal cause is recorded, further cancellations must not interrupt
    cleanup or replace it. Without one, keep ordinary caller cancellation.
    """
    interrupted = await _settle_tasks_without_abandoning(list(tasks))
    state = _fatal_state.get()
    if interrupted is not None and (state is None or state.error is None):
        raise interrupted


class _FixedLease:
    def __init__(self, provider: "FixedCapacity") -> None:
        self._provider = provider
        self._released = False

    async def release(self, report: ExecutionReport) -> None:
        if self._released:
            return
        self._released = True
        await self._provider.release(report)


class FixedCapacity:
    """A fair, event-loop-bound fixed capacity provider."""

    def __init__(self, concurrency: int) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.concurrency = concurrency
        self._available = concurrency
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._closed: NonContinuableError | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            if (
                self._loop.is_closed()
                and not self._waiters
                and self._available == self.concurrency
            ):
                self._loop = loop
            else:
                raise RuntimeError("FixedCapacity cannot be shared across event loops")
        return loop

    async def acquire(self) -> CapacityLease:
        loop = self._bind_loop()
        if self._closed is not None:
            raise self._closed
        if self._available > 0 and not self._waiters:
            self._available -= 1
            return _FixedLease(self)

        waiter = loop.create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except BaseException:
            if not waiter.done():
                waiter.cancel()
            elif not waiter.cancelled() and waiter.exception() is None:
                # A granted permit must not disappear when its waiter is cancelled.
                self._grant_next_or_release()
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass
            raise
        if self._closed is not None:
            raise self._closed
        return _FixedLease(self)

    async def close(self, error: NonContinuableError) -> None:
        self._bind_loop()
        if self._closed is None:
            self._closed = error
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_exception(self._closed)

    async def release(self, _report: ExecutionReport) -> None:
        self._bind_loop()
        if self._closed is None:
            self._grant_next_or_release()

    def _grant_next_or_release(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.done():
                continue
            waiter.set_result(None)
            return
        self._available = min(self.concurrency, self._available + 1)


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    operation_id: int
    value: T | None = None
    error: OperationError | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


AsyncOperation = Callable[[int], Awaitable[tuple[int, T]]]


class AsyncResultIterator(Protocol[T]):
    """A result stream whose pending operations can be settled on early exit."""

    def __aiter__(self) -> "AsyncResultIterator[T]": ...

    async def __anext__(self) -> OperationResult[T]: ...

    async def aclose(self) -> None: ...


class AsyncExecutor(Protocol):
    async def run(self, operation: Callable[[], Awaitable[T]]) -> T: ...

    def map(
        self, operations: Iterable[AsyncOperation[T]],
    ) -> AsyncResultIterator[T]: ...


@dataclass(frozen=True)
class _Completed(Generic[T]):
    result: OperationResult[T] | None = None
    fatal: NonContinuableError | None = None
    cancelled: asyncio.CancelledError | None = None
    unexpected: BaseException | None = None
    producer_done: bool = False
    settled: asyncio.Future[None] | None = None


class ConcurrentExecutor:
    """Execute cold async operations through one shared capacity provider."""

    def __init__(self, capacity: CapacityProvider) -> None:
        self._capacity = capacity
        self._loop: asyncio.AbstractEventLoop | None = None
        self._terminal_error: NonContinuableError | None = None
        self._active: set[asyncio.Task[object]] = set()

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            if self._loop.is_closed() and not self._active:
                self._loop = loop
            else:
                raise RuntimeError("ConcurrentExecutor cannot be shared across event loops")

    @fatal_error_boundary
    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._bind_loop()
        if self._terminal_error is not None:
            raise self._terminal_error
        lease = await self._capacity.acquire()
        try:
            return await self._execute(operation, lease)
        finally:
            if self._terminal_error is not None:
                _record_fatal(self._terminal_error)

    async def _execute(
        self,
        operation: Callable[[], Awaitable[T]],
        lease: CapacityLease,
    ) -> T:
        started = time.monotonic()
        task = asyncio.current_task()
        if task is not None:
            self._active.add(cast(asyncio.Task[object], task))
        report = ExecutionReport(0, ExecutionOutcome.CANCELLED)
        try:
            value = await operation()
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.SUCCESS,
            )
            return value
        except asyncio.CancelledError as error:
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.CANCELLED, error,
            )
            raise
        except NonContinuableError as error:
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.FAILED, error,
            )
            await self._terminate(error, current=task)
            raise
        except OperationError as error:
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.FAILED, error,
            )
            raise
        except Exception as error:
            wrapped = OperationError(str(error) or type(error).__name__, cause=error)
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.FAILED, wrapped,
            )
            raise wrapped from error
        except BaseException as error:
            report = ExecutionReport(
                time.monotonic() - started, ExecutionOutcome.FAILED, error,
            )
            raise
        finally:
            try:
                interrupted = await _release_without_abandoning(lease, report)
                if interrupted is not None:
                    raise interrupted
            finally:
                if task is not None:
                    self._active.discard(cast(asyncio.Task[object], task))

    async def _terminate(
        self,
        error: NonContinuableError,
        *,
        current: asyncio.Task[object] | None,
    ) -> None:
        first = self._terminal_error is None
        if first:
            self._terminal_error = error
        assert self._terminal_error is not None
        _record_fatal(self._terminal_error)
        try:
            if first:
                await self._capacity.close(self._terminal_error)
        finally:
            for task in tuple(self._active):
                if task is not current:
                    # Cancellation carries the group's already chosen cause,
                    # including when a later operation also fails fatally.
                    task.cancel(self._terminal_error)

    @fatal_error_boundary
    async def _map(
        self, operations: Iterable[AsyncOperation[T]],
    ) -> AsyncIterator[OperationResult[T]]:
        self._bind_loop()
        iterator = iter(operations)
        # A completed operation keeps its lease until the consumer accepts the
        # result.  Together with this single-slot handoff, that bounds both
        # input consumption and completed-result buffering by executor capacity.
        completed: asyncio.Queue[_Completed[T]] = asyncio.Queue(maxsize=1)
        running: set[asyncio.Task[None]] = set()
        producer: asyncio.Task[None] | None = None
        teardown_requested = False
        release_failures: list[BaseException] = []
        worker_failures: list[BaseException] = []
        outstanding_settlements: set[asyncio.Future[None]] = set()

        async def execute_one(
            operation_id: int,
            operation: AsyncOperation[T],
            lease: CapacityLease,
        ) -> None:
            started = time.monotonic()
            task = asyncio.current_task()
            if task is not None:
                self._active.add(cast(asyncio.Task[object], task))
            report = ExecutionReport(0, ExecutionOutcome.CANCELLED)
            completion: _Completed[T] | None = None
            settlement: asyncio.Future[None] | None = None
            delivery_interrupted: asyncio.CancelledError | None = None
            delivery_enqueued = False
            try:
                try:
                    returned_id, value = await operation(operation_id)
                    if returned_id != operation_id:
                        raise ValueError(
                            f"operation returned id {returned_id}, expected {operation_id}"
                        )
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.SUCCESS,
                    )
                    completion = _Completed(
                        result=OperationResult(operation_id, value=value),
                    )
                except asyncio.CancelledError as error:
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.CANCELLED, error,
                    )
                    if not teardown_requested:
                        completion = (
                            _Completed(fatal=self._terminal_error)
                            if self._terminal_error is not None
                            else _Completed(cancelled=error)
                        )
                except NonContinuableError as error:
                    error.for_operation(operation_id)
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.FAILED, error,
                    )
                    try:
                        await self._terminate(error, current=task)
                    except BaseException:
                        # The terminal cause was recorded before close(). Even
                        # a broken capacity provider must not prevent delivery
                        # or leave the consumer waiting for a permit forever.
                        pass
                    completion = _Completed(fatal=self._terminal_error)
                except OperationError as error:
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.FAILED, error,
                    )
                    completion = _Completed(result=OperationResult(
                        operation_id, error=error.for_operation(operation_id),
                    ))
                except Exception as error:
                    wrapped = OperationError(
                        str(error) or type(error).__name__, cause=error,
                    ).for_operation(operation_id)
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.FAILED, wrapped,
                    )
                    completion = _Completed(result=OperationResult(
                        operation_id, error=wrapped,
                    ))
                except BaseException as error:
                    report = ExecutionReport(
                        time.monotonic() - started, ExecutionOutcome.FAILED, error,
                    )
                    completion = _Completed(unexpected=error)

                if not teardown_requested and completion is not None:
                    settlement = asyncio.get_running_loop().create_future()
                    outstanding_settlements.add(settlement)
                    completion = replace(completion, settled=settlement)
                    try:
                        await completed.put(completion)
                        delivery_enqueued = True
                    except asyncio.CancelledError as error:
                        delivery_interrupted = error
            finally:
                release_error: BaseException | None = None
                try:
                    interrupted = await _release_without_abandoning(lease, report)
                except BaseException as error:
                    release_failures.append(error)
                    release_error = error
                    interrupted = None
                finally:
                    if task is not None:
                        self._active.discard(cast(asyncio.Task[object], task))
                terminal_interruption = interrupted or delivery_interrupted
                settlement_error = release_error or (
                    self._terminal_error
                    if terminal_interruption is not None
                    and self._terminal_error is not None
                    else terminal_interruption
                )
                if settlement is not None:
                    if not delivery_enqueued:
                        settlement.cancel()
                    elif settlement_error is not None:
                        settlement.set_exception(settlement_error)
                    else:
                        settlement.set_result(None)
                if (
                    not teardown_requested
                    and not delivery_enqueued
                    and terminal_interruption is not None
                ):
                    await completed.put(
                        _Completed(fatal=self._terminal_error)
                        if self._terminal_error is not None
                        else _Completed(cancelled=terminal_interruption)
                    )

        def worker_done(task: asyncio.Task[None]) -> None:
            if task not in running:
                return
            running.remove(task)
            try:
                error = task.exception()
            except asyncio.CancelledError as cancelled:
                error = None if teardown_requested else cancelled
            if error is not None:
                # Retrieve every worker exception, even if it escaped before
                # publishing a completion. Wake a consumer blocked on get(); a
                # full queue already provides a wakeup and the next iteration
                # checks worker_failures before waiting again.
                worker_failures.append(error)
                if not teardown_requested and completed.empty():
                    completed.put_nowait(_Completed(
                        fatal=self._terminal_error, unexpected=error,
                    ))

        async def produce() -> None:
            operation_id = 0
            try:
                while True:
                    lease = await self._capacity.acquire()
                    try:
                        operation = next(iterator)
                    except StopIteration as error:
                        interrupted = await _release_without_abandoning(
                            lease, ExecutionReport(0, ExecutionOutcome.SUCCESS),
                        )
                        if interrupted is not None:
                            raise interrupted from error
                        break
                    except BaseException as error:
                        interrupted = await _release_without_abandoning(
                            lease, ExecutionReport(0, ExecutionOutcome.FAILED),
                        )
                        if interrupted is not None:
                            raise interrupted from error
                        raise
                    task = asyncio.create_task(
                        execute_one(operation_id, operation, lease)
                    )
                    running.add(task)
                    task.add_done_callback(worker_done)
                    operation_id += 1
            finally:
                if not teardown_requested:
                    await completed.put(_Completed(producer_done=True))

        producer = asyncio.create_task(produce())
        producer_done = False
        try:
            while True:
                # A settled worker may have returned before its scheduled done
                # callback runs. Observe it now, using the same idempotent path.
                for task in tuple(running):
                    if task.done():
                        worker_done(task)
                if worker_failures:
                    raise worker_failures[0]
                if producer_done and not running and completed.empty():
                    break
                item = await completed.get()
                if item.settled is not None:
                    try:
                        await asyncio.shield(item.settled)
                    finally:
                        if item.settled.done():
                            outstanding_settlements.discard(item.settled)
                if item.producer_done:
                    producer_done = True
                    # The queue handoff can wake this consumer before the
                    # producer task has fully unwound.  Always settle it so an
                    # iterator-raised error or self-cancellation is observable.
                    await asyncio.shield(producer)
                    continue
                if item.fatal is not None:
                    raise item.fatal
                if item.cancelled is not None:
                    if self._terminal_error is not None:
                        raise self._terminal_error
                    raise item.cancelled
                if item.unexpected is not None:
                    raise item.unexpected
                if item.result is not None:
                    yield item.result
        finally:
            teardown_requested = True
            if producer is not None and not producer.done():
                producer.cancel()
            for task in tuple(running):
                task.cancel()
            pending = ([producer] if producer is not None else []) + list(running)
            teardown_interrupted = await _settle_tasks_without_abandoning(
                cast(list[asyncio.Task[object]], pending),
            )
            close = getattr(iterator, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            for settlement in tuple(outstanding_settlements):
                if settlement.done():
                    try:
                        settlement.exception()
                    except asyncio.CancelledError:
                        pass
                    outstanding_settlements.discard(settlement)
            if release_failures:
                raise release_failures[0]
            if worker_failures:
                raise worker_failures[0]
            if teardown_interrupted is not None:
                raise teardown_interrupted

    def map(
        self, operations: Iterable[AsyncOperation[T]],
    ) -> AsyncResultIterator[T]:
        return cast(AsyncResultIterator[T], self._map(operations))


def task_group_error(
    tasks: Iterable[asyncio.Task[Any]],
) -> BaseException | None:
    """Prefer the recorded terminal cause; otherwise preserve real cancellation.

    Read a cancelled task only once: repeated result()/exception() calls can
    discard its cancellation message. Task completion order is not used to
    replace a fatal cause already recorded by the execution group.
    """
    state = _fatal_state.get()
    if state is not None and state.error is not None:
        return state.error
    cancelled: asyncio.CancelledError | None = None
    for task in tasks:
        if not task.done():
            continue
        try:
            error = task.exception()
        except asyncio.CancelledError as error:
            cancelled = cancelled or error
            fatal = _fatal_from_error(error)
        else:
            fatal = _fatal_from_error(error) if error is not None else None
        if fatal is not None:
            _record_fatal(fatal)
            return fatal
    return cancelled
