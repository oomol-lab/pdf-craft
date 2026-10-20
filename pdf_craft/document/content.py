"""Private PCEX item stream and its chapter-file consumer.

The envelope carries only chapter placement; payloads are the existing PCEX
objects. Resource paths and document metadata belong to the enclosing view.
"""
# pylint: disable=protected-access
import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import Element

from ..common import indent, save_xml
from ..common.stream import DiskDelivery
from ..extractor.chapter.chapter import (
    Chapter, FlowItem, Reference, _encode_flow, _encode_reference,
    search_references_in_chapter,
)
from ..extractor.chapter.reader import create_chapters_reader
from ..runtime import IO_DOMAIN, temporary_directory, worker_iter

ContentCallback = Callable[[Element], object]


@dataclass
class ContentItem:
    """An existing flow item or definition, with its source chapter placement."""
    filename: str
    chapter_id: int | None
    level: int
    index: int
    value: FlowItem | Reference | None  # None preserves an empty chapter.


def chapter_filename(chapter: Chapter) -> str:
    return f"chapter_{chapter.id if chapter.id is not None else 'head'}.xml"


async def read_content(chapters: Path) -> AsyncIterator[ContentItem]:
    reader = await IO_DOMAIN.run(create_chapters_reader, chapters)
    async for chapter in worker_iter(reader()):
        filename = chapter_filename(chapter)
        yield ContentItem(filename, chapter.id, chapter.level, -1, None)
        for index, item in enumerate(chapter.flow_items):
            yield ContentItem(filename, chapter.id, chapter.level, index, item)
        for index, reference in enumerate(search_references_in_chapter(chapter)):
            yield ContentItem(filename, chapter.id, chapter.level, index, reference)


class ChapterWriter:
    """Retain only the current chapter; no full-book Python object graph."""
    def __init__(self, path: Path):
        self.path = path
        self.filename = None
        self.root = None
        self.flow = None
        self.references = None

    def append(self, item: ContentItem):
        if item.filename != self.filename:
            self.finish()
            self.filename = item.filename
            self.root = Element("chapter")
            if item.chapter_id is not None:
                self.root.set("id", str(item.chapter_id))
            if item.level != -1:
                self.root.set("level", str(item.level))
            self.flow = Element("flow")
            self.references = Element("references")
            self.root.append(self.flow)
        assert self.references is not None and self.flow is not None
        if isinstance(item.value, Reference):
            self.references.append(_encode_reference(item.value))
        elif item.value is not None:
            self.flow.append(_encode_flow(item.value))

    def finish(self):
        if self.root is not None:
            assert self.references is not None and self.filename is not None
            if len(self.references):
                self.root.append(self.references)
            self.path.mkdir(parents=True, exist_ok=True)
            save_xml(indent(self.root), self.path / self.filename)
            self.root = None


async def consume_content(
    items: AsyncIterable[ContentItem], chapters: Path | None = None,
    *, on_body: ContentCallback | None = None,
    on_footnote: ContentCallback | None = None,
    aborted=lambda: False,
) -> None:
    """Persist and optionally deliver detached XML, without callback backpressure
    holding up a translation producer's execution capacity.
    """
    writer = ChapterWriter(chapters) if chapters is not None else None
    async with temporary_directory("pdf-craft-content-delivery-") as directory:
        body = DiskDelivery(Path(directory) / "body", on_body) if on_body is not None else None
        notes = DiskDelivery(Path(directory) / "notes", on_footnote) if on_footnote is not None else None
        deliveries = [delivery for delivery in (body, notes) if delivery is not None]
        iterator = aiter(items)
        async def guarded():
            if not deliveries:
                async for item in iterator:
                    yield item
                return
            while True:
                incoming = asyncio.ensure_future(anext(iterator))
                try:
                    done, _ = await asyncio.wait(
                        [incoming, *(delivery._task for delivery in deliveries)],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for delivery in deliveries:
                        if delivery._task in done:
                            delivery._task.result()
                    try:
                        item = incoming.result()
                    except StopAsyncIteration:
                        return
                finally:
                    if not incoming.done():
                        incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
                yield item

        try:
            async for item in guarded():
                from ..metering import check_aborted
                check_aborted(aborted)
                if writer is not None:
                    await IO_DOMAIN.run(writer.append, item)
                if isinstance(item.value, Reference):
                    if notes is not None:
                        await notes.append(await IO_DOMAIN.run(_encode_reference, item.value))
                elif item.value is not None and body is not None:
                    await body.append(await IO_DOMAIN.run(_encode_flow, item.value))
            if writer is not None:
                await IO_DOMAIN.run(writer.finish)
            for delivery in deliveries:
                await delivery.finish()
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()
            for delivery in deliveries:
                await delivery.cancel()
