"""Shared asynchronous capacity control for remote operations."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Generic, Protocol, TypeVar, cast


T = TypeVar("T")


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

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._bind_loop()
        if self._terminal_error is not None:
            raise self._terminal_error
        lease = await self._capacity.acquire()
        return await self._execute(operation, lease)

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
        if self._terminal_error is None:
            self._terminal_error = error
            await self._capacity.close(error)
        for task in tuple(self._active):
            if task is not current:
                task.cancel()

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
        settlements: list[asyncio.Future[None]] = []

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
                    await self._terminate(error, current=task)
                    completion = _Completed(fatal=error)
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
                    settlements.append(settlement)
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
                    task.add_done_callback(running.discard)
                    operation_id += 1
            finally:
                if not teardown_requested:
                    await completed.put(_Completed(producer_done=True))

        producer = asyncio.create_task(produce())
        producer_done = False
        try:
            while True:
                running.difference_update(
                    task for task in tuple(running) if task.done()
                )
                if producer_done and not running and completed.empty():
                    break
                item = await completed.get()
                if item.settled is not None:
                    await asyncio.shield(item.settled)
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
            for settlement in settlements:
                if settlement.done():
                    try:
                        settlement.exception()
                    except asyncio.CancelledError:
                        pass
            if release_failures:
                raise release_failures[0]
            if teardown_interrupted is not None:
                raise teardown_interrupted

    def map(
        self, operations: Iterable[AsyncOperation[T]],
    ) -> AsyncResultIterator[T]:
        return cast(AsyncResultIterator[T], self._map(operations))
