"""Optional paragraph notifications derived from the same items a consumer renders."""
# pylint: disable=protected-access
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

from ..common.stream import DiskDelivery
from ..document.paragraph import ParagraphContent, SourceRegion
from ..document.render import RenderMode, _compose_chapter
from ..extractor.chapter.chapter import (
    Chapter, Reference, TextFlowItem, SourceTextFragment, encode, decode,
)
from ..markdown.render.layouts import render_layouts
from ..runtime import IO_DOMAIN, temporary_directory


def display_values(item, mode):
    """Apply bilingual presentation locally, never to persisted replacement content."""
    if item.value is None:
        return []
    if item.original is None:
        return [item.value]
    if mode == RenderMode.SOURCE:
        mode = RenderMode.REPLACE
    reference = isinstance(item.value, Reference)
    def chapter(value):
        return Chapter(item.chapter_id, item.level, [] if reference else [deepcopy(value)],
                       [deepcopy(value)] if reference else [])
    source, target = encode(chapter(item.original)), encode(chapter(item.value))
    identities = set()
    for element in target.iter('text'):
        fragment = element.find('fragment')
        if fragment is not None:
            identities.add((str(item.chapter_id) if item.chapter_id is not None else 'head',
                            int(fragment.get('page_index', '0')), int(fragment.get('source_order', '0'))))
    preserved = set()
    if item.narrative_states is not None:
        preserved = {identity for identity in identities if item.narrative_states.get(identity) == 'preserved'}
        identities = {identity for identity in identities if item.narrative_states.get(identity) == 'translated'}
    if item.asset_children is None:
        children = range(len(item.value.children)) if isinstance(item.value, TextFlowItem) else [-1]
    else:
        children = item.asset_children
    assets = {(str(item.chapter_id) if item.chapter_id is not None else 'head', 0, index) for index in children}
    _compose_chapter(source, target, identities, assets, mode, preserved)
    result = decode(source)
    return result.definitions if reference else result.flow_items


class ParagraphNotifier:
    """Encode only requested channels; all user-owned values are detached."""
    def __init__(self, pages, assets, numbers, body, notes, scratch):
        self.pages, self.assets, self.numbers = pages, assets, numbers
        self.body, self.notes, self.scratch = body, notes, scratch

    def markdown(self, paragraph, level):
        return ''.join(render_layouts([paragraph], self.assets, self.scratch,
                                      Path('assets'), level, self.numbers))

    def content(self, paragraph, original, level):
        regions = [SourceRegion(self.pages[fragment.page_index], fragment.source_order, fragment.bbox)
                   for fragment in paragraph.children if isinstance(fragment, SourceTextFragment)]
        return ParagraphContent(self.markdown(paragraph, level),
                                self.markdown(original, level) if original is not None else None, regions)

    async def iterate(self, items):
        """Observe callback failure even while the next producer item is pending."""
        import asyncio
        iterator = aiter(items)
        tasks = [delivery._task for delivery in (self.body, self.notes) if delivery is not None]
        try:
            while True:
                incoming = asyncio.ensure_future(anext(iterator))
                try:
                    done, _ = await asyncio.wait([incoming, *tasks], return_when=asyncio.FIRST_COMPLETED)
                    for task in tasks:
                        if task in done:
                            task.result()
                    try:
                        item = incoming.result()
                    except StopAsyncIteration:
                        return
                finally:
                    if not incoming.done():
                        incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
                yield item
        finally:
            close = getattr(iterator, 'aclose', None)
            if close is not None:
                await close()

    async def append(self, item):
        from dataclasses import replace
        if (self.notes is None if isinstance(item.value, Reference) else self.body is None):
            return
        values = display_values(item, RenderMode.REPLACE)
        if values:
            item = replace(item, value=values[0])
        if isinstance(item.value, Reference):
            if self.notes is None:
                return
            originals = item.original.flow_items if isinstance(item.original, Reference) else []
            for index, paragraph in enumerate(item.value.flow_items):
                if isinstance(paragraph, TextFlowItem):
                    original = originals[index] if index < len(originals) else None
                    await self.notes.append(await IO_DOMAIN.run(self.content, paragraph, original, item.level))
        elif isinstance(item.value, TextFlowItem) and self.body is not None:
            await self.body.append(await IO_DOMAIN.run(self.content, item.value, item.original, item.level))


@asynccontextmanager
async def paragraph_notifications(pages, assets, numbers, on_body=None, on_footnote=None):
    async with temporary_directory('pdf-craft-paragraphs-') as directory:
        scratch = Path(directory) / 'assets'
        await IO_DOMAIN.run(scratch.mkdir)
        body = DiskDelivery(Path(directory) / 'body', on_body) if on_body is not None else None
        notes = DiskDelivery(Path(directory) / 'notes', on_footnote) if on_footnote is not None else None
        deliveries = [delivery for delivery in (body, notes) if delivery is not None]
        try:
            yield ParagraphNotifier(pages, assets, numbers, body, notes, scratch)
            for delivery in deliveries:
                await delivery.finish()
        finally:
            for delivery in deliveries:
                await delivery.cancel()


async def notify_extraction(source, target, on_body=None, on_footnote=None):
    """Adapt legacy whole-chapter extensions to the same public paragraph values."""
    from dataclasses import replace
    from ..document.stream_view import content_view
    from ..document.content import read_content
    from ..runtime import worker_context
    async with content_view(source) as (_, pages, numbers, originals):
        async with worker_context(target._materialize()) as paths:
            async with paragraph_notifications(pages, paths.assets, numbers, on_body, on_footnote) as notify:
                async for item in read_content(paths.chapters):
                    original = await anext(originals)
                    await notify.append(replace(item, original=original.value))
