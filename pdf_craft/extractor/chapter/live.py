"""Bridge persisted OCR completions to ordered, incremental chapter analysis."""

import asyncio
from contextlib import aclosing
from pathlib import Path

from ...concurrency import settle_tasks
from ...common import XMLReader, read_xml
from ...common.stream import DiskDelivery, DiskStream
from ...pdf import Page, decode
from ...pdf.ocr import OCREventKind
from ...runtime import IO_DOMAIN, ExecutionDomain, callback_bridge
from .stream import resolve_to_disk
from .primary import primary_page
from ..refinement import MAX_INPUT_LEAD


class LiveExtraction:
    """Only page-order metadata stays in RAM; all content queues live on disk."""

    def __init__(self, path: Path, pages_path: Path, includes_footnotes: bool,
                 on_body=None, on_footnote=None, *, refinement=None) -> None:
        self.pages_path = pages_path
        self.database = path / "content.sqlite"
        self.pages: DiskStream[Page] = DiskStream(path / "pages")
        self.order: list[int] = []
        self.ready: set[int] = set()
        self.ignored: set[int] = set()
        self.position = 0
        self.analysis_error: Exception | None = None
        self.deliveries = []
        self._lock = asyncio.Lock()
        self._stopped = False
        self._finishing: asyncio.Task | None = None
        self._failure: BaseException | None = None
        self._refining = refinement is not None
        self._consumed = 0
        self._capacity = asyncio.Event()
        # A blocking stream consumer must never occupy the pool that feeds it.
        # One owned worker per live stream; joined before its workspace is freed.
        self._analysis_domain = ExecutionDomain("live-analysis", 1)
        loop = asyncio.get_running_loop()

        def callback(kind, handler):
            if handler is None:
                return None
            async def notify(value):
                from ...runtime import invoke_callback
                try:
                    await invoke_callback(handler, value)
                except Exception as error:
                    setattr(error, '_pdf_craft_callback', True)
                    raise
            delivery = DiskDelivery(path / kind, notify)
            self.deliveries.append(delivery)
            return callback_bridge(loop, delivery.append)

        body = callback("body", on_body)
        footnote = callback("footnote", on_footnote)
        self.task = asyncio.create_task(self._refine(refinement, on_body, on_footnote)) if refinement is not None else asyncio.create_task(self._analysis_domain.run(
            resolve_to_disk, self.pages.read, self.database,
            includes_footnotes=includes_footnotes, on_body=body, on_footnote=footnote,
        ))

        self.task.add_done_callback(lambda _task: self._capacity.set())

    async def _refine(self, factory, on_body, on_footnote):
        from .assembly import PageAssembly
        from ..refinement import refine_pages, PageResult
        # Reuse the owned delivery queues, but never route callback coroutines
        # through a blocking worker when the producer already runs on the loop.
        body = self.deliveries[0].append if on_body is not None else None
        note = self.deliveries[-1].append if on_footnote is not None else None
        loop = asyncio.get_running_loop()
        assembly = await IO_DOMAIN.run(lambda: PageAssembly(
            self.database, on_body=callback_bridge(loop, body) if body else None,
            on_footnote=callback_bridge(loop, note) if note else None))
        async def primary():
            previous = previous_analysis = None
            async for page in self.pages.aread():
                analysis = await IO_DOMAIN.run(primary_page, previous, page, None, previous_analysis)
                yield analysis
                previous, previous_analysis = page, analysis
        async with factory() as process, aclosing(refine_pages(primary(), process)) as results:
            async for result in results:
                result = result if isinstance(result, PageResult) else PageResult(result)
                try:
                    await self._analysis_domain.run(assembly.append, result.page)
                except Exception as error:
                    if result.usage is not None:
                        result.usage.settle(False)
                    from ..recovery import recoverable_structure
                    if result.initial is None or not recoverable_structure(error):
                        raise
                    from ..._policy import recover
                    from ...policies import FailureCategory, FailureLocation
                    await recover(error, FailureCategory.ANALYSIS, 'footnote_commit', 'algorithm_result',
                                  targets=(FailureLocation('page', page_indexes=(result.page.page_index,)),))
                    await self._analysis_domain.run(assembly.append, result.initial)
                    result.usage = None
                except BaseException:
                    if result.usage is not None:
                        result.usage.settle(False)
                    raise
                if result.usage is not None:
                    result.usage.settle(True)
                self._consumed += 1
                self._capacity.set()
        await self._analysis_domain.run(assembly.finish)

    async def _wait_capacity(self, ordinal):
        while self._refining:
            self._capacity.clear()
            if self.task.done():
                try:
                    await self.task
                except Exception as error:
                    self._record_error(error)
                return
            if ordinal < self._consumed + MAX_INPUT_LEAD:
                return
            await self._capacity.wait()

    async def event(self, event):
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._stopped:
                raise RuntimeError("extraction stream is closed")
            try:
                await self._event(event)
                ordinal = len(self.order) - 1
            except BaseException as error:
                self._failure = error
                self.pages.close(error)
                raise
        # Admission waits outside the consumption lock: an older OCR request
        # must remain able to publish the input that makes this window advance.
        if event.kind == OCREventKind.START:
            await self._wait_capacity(ordinal)

    async def _event(self, event):
        if self.task.done():
            try:
                await self.task
            except Exception as error:
                self._record_error(error)
        if self.analysis_error is not None:
            return
        if event.kind == OCREventKind.START:
            self.order.append(event.page_index)
        elif event.kind == OCREventKind.IGNORE:
            self.ignored.add(event.page_index)
        elif event.kind in (OCREventKind.COMPLETE, OCREventKind.SKIP, OCREventKind.FAILED):
            self.ready.add(event.page_index)
        while self.position < len(self.order):
            index = self.order[self.position]
            if index in self.ignored:
                self.ignored.remove(index)
            elif index in self.ready:
                try:
                    page = await IO_DOMAIN.run(decode, await IO_DOMAIN.run(
                        read_xml, self.pages_path / f"page_{index}.xml"))
                except Exception as error:
                    setattr(error, 'page_index', index)
                    self._record_error(error)
                    return
                await IO_DOMAIN.run(self.pages.append, page)
                self.ready.remove(index)
            else:
                break
            self.position += 1

    async def finish(self):
        async with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._finishing is None:
                if self._stopped:
                    raise RuntimeError("extraction stream is closed")
                self._stopped = True
                self._finishing = asyncio.create_task(self._finish())
            finishing = self._finishing
        # Do not hold the event lock while waiting for user callbacks: cancel
        # must be able to close input and interrupt those deliveries.
        try:
            await asyncio.shield(finishing)
        except BaseException as error:
            if self._failure is None:
                self._failure = error
            self.pages.close(error)
            finishing.cancel()
            raise

    async def _finish(self):
        if not self.order:
            # A complete OCR cache emits no page events, but is replayable.
            reader = await IO_DOMAIN.run(XMLReader, "page", self.pages_path, decode)
            from ...runtime import worker_iter
            try:
                ordinal = 0
                async for page in worker_iter(reader.read()):
                    await self._wait_capacity(ordinal)
                    await IO_DOMAIN.run(self.pages.append, page)
                    ordinal += 1
            except Exception as error:
                self._record_error(error)
        self.pages.close()
        try:
            await self.task
        except Exception as error:
            self._record_error(error)
        for delivery in self.deliveries:
            await delivery.finish()

    def _record_error(self, error):
        from ..recovery import recoverable_structure
        if not recoverable_structure(error):
            raise error
        self.analysis_error = error

    async def cancel(self):
        async with self._lock:
            self._stopped = True
            self.pages.close(RuntimeError("extraction stream aborted"))
            if self._finishing is not None:
                self._finishing.cancel()
        try:
            for delivery in self.deliveries:
                await delivery.cancel()
        finally:
            try:
                await settle_tasks([self.task] + ([self._finishing] if self._finishing is not None else []))
            finally:
                self._analysis_domain.close()
