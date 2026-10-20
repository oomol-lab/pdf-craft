"""Ordered delivery with bounded execution and disk-backed reorder storage."""

import asyncio
import pickle
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar
from xml.etree.ElementTree import Element

from pdf_craft.concurrency import NonContinuableError
from pdf_craft.runtime import IO_DOMAIN, temporary_directory

P = TypeVar("P")
R = TypeVar("R")


def _source_elements(value, found=None, visited=None):
    """Keep source DOM identity; translated payloads themselves go to disk."""
    found = {} if found is None else found
    visited = set() if visited is None else visited
    if id(value) in visited:
        return found
    visited.add(id(value))
    if isinstance(value, Element):
        found[id(value)] = value
    elif isinstance(value, dict):
        for child in value.values():
            _source_elements(child, found, visited)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _source_elements(child, found, visited)
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        _source_elements(vars(value), found, visited)
    return found


def _save(path, value, sources):
    references = {}

    class Writer(pickle.Pickler):
        def persistent_id(self, obj):
            if isinstance(obj, Element) and id(obj) in sources:
                references[id(obj)] = obj
                return id(obj)
            return None

    with path.open("wb") as file:
        Writer(file, protocol=pickle.HIGHEST_PROTOCOL).dump(value)
    return references


def _load(path, references):
    class Reader(pickle.Unpickler):
        def persistent_load(self, pid):
            return references[pid]

    with path.open("rb") as file:
        value = Reader(file).load()
    path.unlink()
    return value


async def run_concurrency_async(
    parameters: Iterable[P], execute: Callable[[P], Awaitable[R]], concurrency: int,
) -> AsyncGenerator[R, None]:
    """Adapt synchronous input to the one asynchronous scheduler."""
    results = run_async_parameters(parameters, execute, concurrency)
    try:
        async for result in results:
            yield result
    finally:
        await results.aclose()


async def run_async_parameters(parameters, execute, concurrency):
    """Slow ordered output never prevents admission to an idle execution slot.

    Only source-node identity references remain in memory alongside the DOM
    already owned by the adapter. Completed translated content is pickled to
    disk, and is loaded once its input-order position can be delivered.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    asynchronous = isinstance(parameters, AsyncIterable)
    iterator: Any = aiter(parameters) if asynchronous else iter(parameters)
    ready = {}
    changed = asyncio.Event()
    async with temporary_directory("pdf-craft-reorder-") as directory:
        root = Path(directory)

        async def produce():
            pending = {}
            incoming = None
            next_input = 0
            exhausted = False
            try:
                while not exhausted or pending or incoming is not None:
                    if not asynchronous:
                        while not exhausted and len(pending) < concurrency:
                            try:
                                parameter = next(iterator)
                            except StopIteration:
                                exhausted = True
                                break
                            task = asyncio.create_task(execute(parameter))
                            pending[task] = (next_input, _source_elements(parameter))
                            next_input += 1
                    elif not exhausted and incoming is None and len(pending) < concurrency:
                        incoming = asyncio.ensure_future(anext(iterator))
                    waiting = set(pending)
                    if incoming is not None:
                        waiting.add(incoming)
                    if not waiting:
                        break
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    failures = [task.exception() for task in done
                                if task in pending and not task.cancelled() and task.exception() is not None]
                    if failures:
                        raise next((error for error in failures
                                    if isinstance(error, NonContinuableError)), failures[0])
                    if incoming is not None and incoming in done:
                        try:
                            parameter = incoming.result()
                        except StopAsyncIteration:
                            exhausted = True
                        else:
                            sources = _source_elements(parameter)
                            task = asyncio.create_task(execute(parameter))
                            pending[task] = (next_input, sources)
                            next_input += 1
                        incoming = None
                    for task in done:
                        if task not in pending:
                            continue
                        index, sources = pending.pop(task)
                        references = await IO_DOMAIN.run(_save, root / str(index), task.result(), sources)
                        ready[index] = references
                        changed.set()
            finally:
                tasks = list(pending)
                if incoming is not None:
                    tasks.append(incoming)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if asynchronous:
                    close = getattr(iterator, "aclose", None)
                    if close is not None:
                        await close()
                else:
                    close = getattr(iterator, "close", None)
                    if close is not None:
                        close()
                changed.set()

        producer = asyncio.create_task(produce())
        index = 0
        try:
            while True:
                changed.clear()
                if producer.done():
                    producer.result()
                if index in ready:
                    references = ready.pop(index)
                    yield await IO_DOMAIN.run(_load, root / str(index), references)
                    index += 1
                elif producer.done():
                    return
                else:
                    await changed.wait()
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
