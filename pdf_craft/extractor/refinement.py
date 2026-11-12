"""Readiness-driven page jobs, followed by an ordered semantic consumer."""
import asyncio
import pickle
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..concurrency import fatal_error_boundary, settle_tasks
from ..runtime import IO_DOMAIN
from ..progress import RefinementStatus

MAX_PAGE_JOBS = 8
MAX_INPUT_LEAD = MAX_PAGE_JOBS + 4


@dataclass
class PageResult:
    """A validated page candidate and its deferred resource accounting."""
    page: Any
    usage: Any = None
    initial: Any = None
    refinement_status: RefinementStatus = RefinementStatus.NOT_REQUIRED


async def refine_ready(pages, process, directory, order, *, window=MAX_PAGE_JOBS, includes_footnotes=True):
    """Only raw neighbors gate requests; results are delivered without semantic rebinding.

    ``order`` supplies the complete selected sequence from OCR page count.
    An empty sequence also supports direct/cache streams in source order.
    Raw inputs and completed candidates are spooled, not retained by tasks.
    """
    if window < 1:
        raise ValueError('window must be positive')
    directory.mkdir(parents=True, exist_ok=True)
    arrived, scheduled, completed = set(), set(), set()
    discovered = []
    body_boundaries = {}
    changed = asyncio.Event()
    running, usages = {}, {}
    position = 0

    def save(name, value):
        with (directory / name).open('wb') as file:
            pickle.dump(value, file)

    def load(name):
        with (directory / name).open('rb') as file:
            return pickle.load(file)

    async def scan():
        async with aclosing(pages):
            async for page in pages:
                def store_input(page=page):
                    save(f'raw-{page.index}', page)
                    return _body_boundary(page, includes_footnotes)
                body_boundaries[page.index] = await IO_DOMAIN.run(store_input)
                discovered.append(page.index)
                arrived.add(page.index)
                changed.set()

    async def execute(previous, index, following):
        def context():
            from .chapter.primary import PrimaryPages
            raw = tuple(load(f'raw-{key}') if key is not None else None
                        for key in (previous, index, following))
            neighbors = _project_window(raw, includes_footnotes)
            # Build the raw proposal before the job, never on result delivery.
            # Scan scalar input identities backwards, holding at most the last
            # body page and the adjacent page. Missing older OCR never delays
            # a job whose immediate raw neighbours are ready.
            primary = PrimaryPages(includes_footnotes)
            primary.previous = raw[0]
            sequence = order() or discovered
            ordinal = sequence.index(index)
            for key in reversed(sequence[:ordinal]):
                if key not in arrived:
                    break
                if body_boundaries[key]:
                    primary.body_tail = load(f'raw-{key}')
                    break
            return neighbors[0], primary.append(raw[1], following=raw[2]), neighbors[2]
        neighbors = await IO_DOMAIN.run(context)
        result = await process(*deepcopy(neighbors))
        result = result if isinstance(result, PageResult) else PageResult(result)
        # Usage scopes hold locks/provider identity; retain only their small
        # accounting records in memory, while candidate DOMs live on disk.
        usages[index] = result.usage
        result.usage = None
        try:
            await IO_DOMAIN.run(save, f'result-{index}', result)
        except BaseException:
            if usages[index] is not None:
                usages.pop(index).settle(False)
            raise
        from .progress import page_stage
        await page_stage(index, 7, refinement_status=result.refinement_status)
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
                result = await IO_DOMAIN.run(load, f'result-{index}')
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


def _project_window(pages, includes_footnotes=True):
    from .chapter.primary import project_window
    return project_window(pages, includes_footnotes)


def _body_boundary(page, includes_footnotes):
    """Raw input routing only; this scalar is never used in acceptance."""
    from .chapter.marginal_numbers import reading_body
    from .chapter.source import _is_empty_text_layout
    body = reading_body(page, includes_footnotes=includes_footnotes)
    return (not body or not any(not _is_empty_text_layout(item) for item in body)
            or any(not _is_empty_text_layout(item) and item.ref not in ('image', 'table') for item in body))
