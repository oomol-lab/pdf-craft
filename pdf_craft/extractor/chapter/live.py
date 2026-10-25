"""Bridge persisted OCR completions to ordered, incremental chapter analysis."""

import asyncio
from pathlib import Path

from ...concurrency import settle_tasks
from ...common import XMLReader, read_xml
from ...common.stream import DiskDelivery, DiskStream
from ...pdf import Page, decode
from ...pdf.ocr import OCREventKind
from ...runtime import IO_DOMAIN, callback_bridge
from .stream import resolve_to_disk


class LiveExtraction:
    """Only page-order metadata stays in RAM; all content queues live on disk."""

    def __init__(self, path: Path, pages_path: Path, includes_footnotes: bool,
                 on_body=None, on_footnote=None) -> None:
        self.pages_path = pages_path
        self.database = path / "content.sqlite"
        self.pages: DiskStream[Page] = DiskStream(path / "pages")
        self.order: list[int] = []
        self.ready: set[int] = set()
        self.ignored: set[int] = set()
        self.position = 0
        self.analysis_error: Exception | None = None
        self.deliveries = []
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
        self.task = asyncio.create_task(IO_DOMAIN.run(
            resolve_to_disk, self.pages.read, self.database,
            includes_footnotes=includes_footnotes, on_body=body, on_footnote=footnote,
        ))

    async def event(self, event):
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
        if not self.order:
            # A complete OCR cache emits no page events, but is replayable.
            reader = await IO_DOMAIN.run(XMLReader, "page", self.pages_path, decode)
            from ...runtime import worker_iter
            try:
                async for page in worker_iter(reader.read()):
                    await IO_DOMAIN.run(self.pages.append, page)
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
        self.pages.close(RuntimeError("extraction stream aborted"))
        await settle_tasks([self.task])
        for delivery in self.deliveries:
            await delivery.cancel()
