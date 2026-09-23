"""Bounded concurrency helpers for XML translation.

The synchronous helper deliberately stays sequential. Network concurrency is
owned by the asynchronous pipeline; running one event loop per worker thread
would make cancellation and the LLM semaphore ineffective.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from typing import TypeVar

P = TypeVar("P")
R = TypeVar("R")


def run_concurrency(
    parameters: Iterable[P],
    execute: Callable[[P], R],
    concurrency: int,
) -> Iterator[R]:
    assert concurrency >= 1, "the concurrency must be at least 1"
    for parameter in parameters:
        yield execute(parameter)


async def run_concurrency_async(
    parameters: Iterable[P],
    execute: Callable[[P], Awaitable[R]],
    concurrency: int,
) -> AsyncIterator[R]:
    """Execute at most ``concurrency`` awaitables, yielding in input order."""
    assert concurrency >= 1, "the concurrency must be at least 1"
    iterator = iter(parameters)
    pending: deque[asyncio.Future[R]] = deque()
    try:
        for _ in range(concurrency):
            try:
                parameter = next(iterator)
            except StopIteration:
                break
            pending.append(asyncio.ensure_future(execute(parameter)))

        while pending:
            yield await pending.popleft()
            try:
                parameter = next(iterator)
            except StopIteration:
                continue
            pending.append(asyncio.ensure_future(execute(parameter)))
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
