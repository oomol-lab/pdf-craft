"""Readiness-driven page jobs, followed by an ordered semantic consumer."""
import asyncio
import pickle
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..concurrency import fatal_error_boundary, settle_tasks
from ..runtime import IO_DOMAIN

MAX_PAGE_JOBS = 8
MAX_INPUT_LEAD = MAX_PAGE_JOBS + 4


@dataclass
class PageResult:
    """A validated page candidate and its deferred resource accounting."""
    page: Any
    usage: Any = None
    initial: Any = None


async def refine_ready(pages, process, directory, order, *, window=MAX_PAGE_JOBS):
    """Only raw neighbors gate requests; global identities are bound on delivery.

    ``order`` supplies the complete selected sequence from OCR page count.
    An empty sequence also supports direct/cache streams in source order.
    Raw inputs and completed candidates are spooled, not retained by tasks.
    """
    from .chapter.primary import PrimaryPages, bind_local_result
    if window < 1:
        raise ValueError('window must be positive')
    directory.mkdir(parents=True, exist_ok=True)
    arrived, scheduled, completed = set(), set(), set()
    discovered = []
    changed = asyncio.Event()
    running, usages = {}, {}
    position = 0
    initial = PrimaryPages()

    def save(name, value):
        with (directory / name).open('wb') as file:
            pickle.dump(value, file)

    def load(name):
        with (directory / name).open('rb') as file:
            return pickle.load(file)

    async def scan():
        async with aclosing(pages):
            async for page in pages:
                await IO_DOMAIN.run(save, f'raw-{page.index}', page)
                discovered.append(page.index)
                arrived.add(page.index)
                changed.set()

    async def execute(previous, index, following):
        def context():
            return _project_window(tuple(load(f'raw-{key}') if key is not None else None
                                         for key in (previous, index, following)))
        neighbors = await IO_DOMAIN.run(context)
        result = await process(*deepcopy(neighbors))
        result = result if isinstance(result, PageResult) else PageResult(result)
        # Usage scopes hold locks/provider identity; retain only their small
        # accounting records in memory, while candidate DOMs live on disk.
        usages[index] = result.usage
        result.usage = None
        try:
            await IO_DOMAIN.run(save, f'result-{index}', (result, neighbors[:2]))
        except BaseException:
            if usages[index] is not None:
                usages.pop(index).settle(False)
            raise
        completed.add(index)

    scanner = asyncio.create_task(fatal_error_boundary(scan)())
    scanner.add_done_callback(lambda _task: changed.set())
    try:
        while True:
            changed.clear()
            if scanner.done():
                scanner.result()  # Failed/cancelled input is never EOF.
            for index, task in list(running.items()):
                if task.done():
                    task.result()
                    del running[index]
            expected = order()
            sequence = expected or discovered
            # Limit both active work and out-of-order lead. In particular,
            # completed later pages must not let OCR race a whole book ahead.
            for ordinal in range(position, min(len(sequence), position + MAX_INPUT_LEAD - 1)):
                index = sequence[ordinal]
                if len(running) >= window:
                    break
                if index in scheduled or index not in arrived:
                    continue
                if ordinal + 1 == len(sequence) and not (expected or scanner.done()):
                    continue  # The right boundary is not known yet.
                previous = sequence[ordinal - 1] if ordinal else None
                following = sequence[ordinal + 1] if ordinal + 1 < len(sequence) else None
                if any(key is not None and key not in arrived for key in (previous, following)):
                    continue
                task = asyncio.create_task(fatal_error_boundary(execute)(previous, index, following))
                task.add_done_callback(lambda _task: changed.set())
                running[index] = task
                scheduled.add(index)
            if position < len(sequence) and sequence[position] in completed:
                index = sequence[position]
                def bind(index=index):
                    result, (previous, local) = load(f'result-{index}')
                    canonical_previous = initial.previous_analysis
                    canonical = initial.append(load(f'raw-{index}'))
                    result.page = bind_local_result(result.page, local, previous, canonical, canonical_previous)
                    if result.initial is not None:
                        result.initial = canonical
                    return result
                result = await IO_DOMAIN.run(bind)
                result.usage = usages.pop(index)
                try:
                    yield result
                finally:
                    if result.usage is not None:
                        result.usage.settle(False)  # No-op after consumer settlement.
                position += 1
                continue
            if (expected or scanner.done()) and position == len(sequence):
                return
            if scanner.done() and not running:
                raise RuntimeError('OCR ended before all registered pages became ready')
            await changed.wait()
    finally:
        tasks = [scanner, *running.values()]
        for task in tasks:
            task.cancel()
        await settle_tasks(tasks)
        for usage in usages.values():
            if usage is not None:
                usage.settle(False)


def _project_window(pages):
    from .chapter.primary import PrimaryPages
    projection = PrimaryPages()
    return tuple(projection.append(page) if page is not None else None for page in pages)
