"""Bounded concurrency helpers for XML translation.

The synchronous helper deliberately stays sequential. Network concurrency is
owned by the asynchronous pipeline; running one event loop per worker thread
would make cancellation and the LLM semaphore ineffective.
"""

import asyncio
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
    pending: dict[asyncio.Future[R], int] = {}
    completed: dict[int, R] = {}
    next_input = 0
    next_output = 0

    def submit(parameter: P) -> None:
        nonlocal next_input
        pending[asyncio.ensure_future(execute(parameter))] = next_input
        next_input += 1

    try:
        for _ in range(concurrency):
            try:
                parameter = next(iterator)
            except StopIteration:
                break
            submit(parameter)

        while pending:
            done, _ = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                index = pending.pop(task)
                completed[index] = task.result()
                try:
                    parameter = next(iterator)
                except StopIteration:
                    pass
                else:
                    submit(parameter)
            while next_output in completed:
                yield completed.pop(next_output)
                next_output += 1
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending.keys(), return_exceptions=True)
