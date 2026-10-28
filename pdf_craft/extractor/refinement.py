"""Bounded local page jobs. Neighbors always belong to the initial input stream."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..concurrency import fatal_error_boundary, settle_tasks

MAX_PAGE_JOBS = 8
MAX_INPUT_LEAD = MAX_PAGE_JOBS + 4


@dataclass
class PageResult:
    """A validated page candidate and its deferred resource accounting."""
    page: Any
    usage: Any = None
    initial: Any = None


async def refine_pages(pages, process, *, window=MAX_PAGE_JOBS):
    """Process independent three-page windows; deliver in order with backpressure."""
    if window < 1:
        raise ValueError('window must be positive')
    slots = asyncio.Semaphore(window)
    ready = asyncio.Queue()
    tasks = set()
    failed = asyncio.get_running_loop().create_future()

    def completed(task):
        if not task.cancelled() and task.exception() is not None and not failed.done():
            failed.set_result(task.exception())

    @fatal_error_boundary
    async def produce():
        iterator = aiter(pages)
        try:
            previous = None
            current = await anext(iterator, None)
            following = await anext(iterator, None) if current is not None else None
            while current is not None:
                await slots.acquire()
                task = asyncio.create_task(fatal_error_boundary(process)(*deepcopy((previous, current, following))))
                tasks.add(task)
                task.add_done_callback(completed)
                ready.put_nowait(task)
                previous, current = current, following
                following = await anext(iterator, None) if current is not None else None
            ready.put_nowait(None)
        finally:
            await iterator.aclose()

    producer = asyncio.create_task(produce())
    producer.add_done_callback(completed)
    waiter = None
    try:
        while True:
            waiter = asyncio.create_task(ready.get())
            await asyncio.wait([waiter, failed], return_when=asyncio.FIRST_COMPLETED)
            if failed.done():
                raise failed.result()
            task = waiter.result()
            if task is None:
                await producer
                return
            await asyncio.wait([task, failed], return_when=asyncio.FIRST_COMPLETED)
            if failed.done():
                raise failed.result()
            result = task.result()
            tasks.remove(task)
            yield result
            slots.release()
    finally:
        owned = [producer, *tasks] + ([waiter] if waiter is not None else [])
        for task in owned:
            task.cancel()
        await settle_tasks(owned)
