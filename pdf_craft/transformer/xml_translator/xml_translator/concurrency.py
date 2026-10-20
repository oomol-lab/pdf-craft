"""Bounded asynchronous concurrency for XML translation."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from typing import TypeVar

from pdf_craft.concurrency import NonContinuableError

P = TypeVar("P")
R = TypeVar("R")


async def run_concurrency_async(
    parameters: Iterable[P],
    execute: Callable[[P], Awaitable[R]],
    concurrency: int,
) -> AsyncGenerator[R, None]:
    """Execute at most ``concurrency`` awaitables, yielding in input order."""
    assert concurrency >= 1, "the concurrency must be at least 1"
    iterator = iter(parameters)
    pending: dict[asyncio.Future[R], int] = {}
    completed: dict[int, R] = {}
    next_input = 0
    next_output = 0
    exhausted = False

    def submit(parameter: P) -> None:
        nonlocal next_input
        pending[asyncio.ensure_future(execute(parameter))] = next_input
        next_input += 1

    def fill_window() -> None:
        nonlocal exhausted
        # Keep at most ``concurrency`` later results ahead of the next ordered
        # output.  The extra slot represents that next output itself.
        while (
            not exhausted
            and len(pending) < concurrency
            and len(pending) + len(completed) < concurrency + 1
        ):
            try:
                parameter = next(iterator)
            except StopIteration:
                exhausted = True
                return
            submit(parameter)

    try:
        fill_window()
        while pending or completed:
            fill_window()
            if next_output in completed:
                result = completed.pop(next_output)
                next_output += 1
                yield result
                continue
            if not pending:
                break
            done, _ = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            failures: list[tuple[int, BaseException]] = []
            was_cancelled = False
            for task in sorted(done, key=pending.__getitem__):
                index = pending.pop(task)
                if task.cancelled():
                    was_cancelled = True
                    continue
                error = task.exception()
                if error is not None:
                    failures.append((index, error))
                else:
                    completed[index] = task.result()
            if failures:
                terminal = next(
                    (error for _, error in failures
                     if isinstance(error, NonContinuableError)),
                    None,
                )
                raise terminal or failures[0][1]
            if was_cancelled:
                raise asyncio.CancelledError
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending.keys(), return_exceptions=True)
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


async def run_async_parameters(parameters, execute, concurrency):
    """Bounded admission for asynchronous filesystem producers."""
    iterator = aiter(parameters)
    pending = {}
    completed = {}
    incoming = None
    next_input = 0
    next_output = 0
    exhausted = False
    try:
        while not exhausted or pending or completed or incoming is not None:
            if (not exhausted and incoming is None and
                    len(pending) < concurrency and len(pending) + len(completed) < concurrency + 1):
                incoming = asyncio.create_task(anext(iterator))
            if next_output in completed:
                yield completed.pop(next_output)
                next_output += 1
                continue
            waiting = set(pending)
            if incoming is not None:
                waiting.add(incoming)
            if not waiting:
                break
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if incoming is not None and incoming in done:
                try:
                    value = incoming.result()
                except StopAsyncIteration:
                    exhausted = True
                else:
                    task = asyncio.create_task(execute(value))
                    pending[task] = next_input
                    next_input += 1
                incoming = None
            failures = []
            for task in done:
                if task in pending:
                    index = pending.pop(task)
                    if task.cancelled():
                        raise asyncio.CancelledError
                    error = task.exception()
                    if error is not None:
                        failures.append(error)
                    else:
                        completed[index] = task.result()
            if failures:
                raise next((error for error in failures if isinstance(error, NonContinuableError)), failures[0])
    finally:
        tasks = list(pending)
        if incoming is not None:
            tasks.append(incoming)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await iterator.aclose()
