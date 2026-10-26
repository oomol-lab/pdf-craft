"""PCEX ready work shares one bounded window; delivery is a separate disk read."""
# pylint: disable=protected-access
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from ..concurrency import fatal_error_boundary, settle_tasks
from ..extractor.chapter.chapter import Reference, TextFlowItem, narrative_texts
from ..runtime import IO_DOMAIN, temporary_directory
from .._failure_sources import page_sources
from .._policy import Completion
from .anchored_translation import _asset_slots, _translate_slots_async
from .translation_coverage import NarrativeCoverage, AnchoredContentCoverage, paragraph_identity, accepted_translation
from .ready_queue import ReadyQueue
from .furniture_plan import FurniturePlan


class PCEXSchedule:
    """Only the running window is in memory. Pending and completed work is on disk."""

    def __init__(self, items, transformer, window, queue, *, pages=None, aborted=lambda: False,
                 with_anchored=False, furniture=None, metadata=None, notifier=None):
        if window < 1:
            raise ValueError('window must be at least 1')
        self.items, self.transformer, self.window, self.queue = items, transformer, window, queue
        self.pages, self.aborted = pages or {}, aborted
        self.with_anchored, self.furniture, self.metadata = with_anchored, furniture, metadata
        self.changed = asyncio.Event()
        self.work_changed = asyncio.Event()
        self.scanned = False
        self.count = 0
        self.consumed = False
        self.overlay = None
        self.headers_ready = asyncio.Event()
        self.notifier = notifier
        self.notifications = {channel: asyncio.Event() for channel in ("body", "footnote")}
        self.completion = Completion()
        self.runner: asyncio.Task | None = None

    def wake(self):
        self.changed.set()
        self.work_changed.set()
        for changed in self.notifications.values():
            changed.set()

    async def scan(self):
        from .content import _unit
        try:
            async for item in self.items:
                index = self.count
                channel = 'footnote' if isinstance(item.value, Reference) else 'body'
                def save(index=index, item=item, channel=channel):
                    self.queue.item(index, item, channel)
                    if item.value is not None:
                        self.queue.add(channel, item, item=index)
                        if self.with_anchored:
                            for number, slot in enumerate(_asset_slots(_unit(item))):
                                slot.payload = replace(slot.payload, flow_index=item.index, context=item.asset_context)
                                self.queue.add('asset', (number, slot), item=index)
                    self.queue.run('UPDATE items SET sealed=1 WHERE id=?', (index,))
                await IO_DOMAIN.run(save)
                self.count += 1
                self.wake()
        finally:
            self.scanned = True
            self.wake()
            await self.items.aclose()

    async def execute(self, job):
        from .content import _unit
        from ..metering import check_aborted
        identifier, kind, payload, _index = job
        check_aborted(self.aborted)
        with page_sources(self.pages):
            if kind == 'metadata':
                assert self.metadata is not None
                result = await self.metadata()
                self.overlay = result
            elif kind == 'furniture':
                assert self.furniture is not None
                result = await self.furniture.execute(payload, self.transformer._furniture_transformer())
            elif kind == 'asset':
                number, slot = payload
                result = number, (await _translate_slots_async([slot], self.transformer._anchored_transformer()))[0], slot.payload.identity
            else:
                source = _unit(payload)
                target = await self.transformer.transform(source)
                states = []
                from .package import _has_visible_content
                for original, translated in zip(narrative_texts(source), narrative_texts(target), strict=True):
                    identity = paragraph_identity(source, original)
                    if identity is not None:
                        states.append(NarrativeCoverage(*identity, 'translated' if
                            _has_visible_content(translated) and accepted_translation(translated) else 'preserved'))
                if self.furniture is not None:
                    await IO_DOMAIN.run(self.furniture.titles, target)
                primary = isinstance(payload.value, Reference) or (isinstance(payload.value, TextFlowItem) and payload.value.role != 'heading')
                if primary:
                    self.completion.succeeded += sum(state.state == 'translated' for state in states)
                    self.completion.degraded += sum(state.state == 'preserved' for state in states)
                result = target, states
        await IO_DOMAIN.run(self.queue.finish, identifier, result)
        self.wake()

    @fatal_error_boundary
    async def run(self):
        scanner = asyncio.create_task(self.scan())
        deliveries = ([asyncio.create_task(self.deliver(channel)) for channel in self.notifications]
                      if self.notifier is not None else [])
        observers = ([delivery._task for delivery in (self.notifier.body, self.notifier.notes) if delivery is not None]
                     if self.notifier is not None else [])
        for task in deliveries + observers:
            task.add_done_callback(lambda _task: self.wake())
        running = set()
        kinds = ['metadata', 'body', 'footnote', 'asset', 'furniture']
        try:
            if self.metadata is not None:
                await IO_DOMAIN.run(self.queue.add, 'metadata', None)
            while True:
                self.work_changed.clear()
                if scanner.done():
                    scanner.result()
                for task in deliveries + observers:
                    if task.done():
                        task.result()
                for task in tuple(running):
                    if task.done():
                        running.remove(task)
                        task.result()
                while len(running) < self.window:
                    job = await IO_DOMAIN.run(self.queue.take, kinds)
                    if job is None:
                        break
                    # Fair admission across independent channels. A blocked
                    # body window cannot hide already scanned notes/fragments.
                    kind = job[1]
                    kinds.remove(kind)
                    kinds.append(kind)
                    task = asyncio.create_task(fatal_error_boundary(self.execute)(job))
                    task.add_done_callback(lambda _task: self.wake())
                    running.add(task)
                if not self.headers_ready.is_set() and not await IO_DOMAIN.run(self.queue.pending, ('metadata', 'furniture')):
                    if self.furniture is not None:
                        await IO_DOMAIN.run(self.furniture.finish)
                    self.headers_ready.set()
                if self.scanned and not running:
                    if not await IO_DOMAIN.run(self.queue.pending):
                        self.completion.check('translation')
                        if all(task.done() for task in deliveries):
                            return
                    # Only unresolved reuse can remain when there is no ready
                    # work and all inputs have been admitted. Missing/fallback
                    # titles wake dependants too, with no trusted translation.
                    if await IO_DOMAIN.run(self.queue.pending):
                        await IO_DOMAIN.run(self.queue.run, 'UPDATE titles SET resolved=1 WHERE resolved=0')
                        self.wake()
                await self.work_changed.wait()
        finally:
            scanner.cancel()
            for task in [*running, *deliveries]:
                task.cancel()
            try:
                await settle_tasks([scanner, *running, *deliveries])
            finally:
                self.wake()

    async def header(self):
        assert self.runner is not None
        waiter = asyncio.create_task(self.headers_ready.wait())
        try:
            await asyncio.wait([waiter, self.runner], return_when=asyncio.FIRST_COMPLETED)
            if self.runner.done():
                self.runner.result()
            return self.overlay or {}
        finally:
            waiter.cancel()
            await settle_tasks([waiter])

    @staticmethod
    def combine(source: Any, results):
        target, states, assets = None, [], []
        for kind, result in results:
            if kind in ('body', 'footnote'):
                target, states = result
        if target is None:
            return source, (), ()
        slots = list(_asset_slots(target))
        for kind, result in results:
            if kind != 'asset':
                continue
            number, translation, identity = result
            slot = slots[number]
            if translation is not None:
                slot.asset.title, slot.asset.content, slot.asset.caption = translation.title, translation.content, translation.caption
            assets.append(AnchoredContentCoverage(identity[0], identity[1], identity[2],
                                                  'translated' if translation is not None else 'preserved'))
        value = target.definitions[0] if isinstance(source.value, Reference) else target.flow_items[0]
        if isinstance(value, Reference):
            value.mark = source.value.mark
        return replace(source, value=value, original=source.value,
                       narrative_states={(state.chapter_id, state.page_index, state.order): state.state for state in states},
                       asset_children=frozenset(state.child_index for state in assets if state.state == 'translated')), states, assets

    async def deliver(self, channel):
        """Each callback channel advances independently of ordered file output."""
        assert self.notifier is not None
        changed = self.notifications[channel]
        while True:
            changed.clear()
            rows = await IO_DOMAIN.run(self.queue.run,
                'SELECT id FROM items WHERE channel=? AND notified=0 ORDER BY id LIMIT 1', (channel,))
            if rows:
                index = rows[0][0]
                result = await IO_DOMAIN.run(self.queue.result, index)
                if result is not None:
                    item, _, _ = await IO_DOMAIN.run(self.combine, *result)
                    await self.notifier.append(item)
                    await IO_DOMAIN.run(self.queue.discard, index, notified=True, observing=True)
                    self.wake()
                    continue
            elif self.scanned:
                return
            await changed.wait()

    async def results(self, coverage, assets, on_completed=None):
        assert self.runner is not None
        index = 0
        while True:
            self.changed.clear()
            if self.runner.done():
                self.runner.result()
            result = await IO_DOMAIN.run(self.queue.result, index)
            if result is not None:
                item, states, asset_states = await IO_DOMAIN.run(self.combine, *result)
                coverage.extend(states)
                assets.extend(asset_states)
                if on_completed is not None:
                    await on_completed(item)
                yield item
                await IO_DOMAIN.run(self.queue.discard, index, observing=self.notifier is not None)
                index += 1
            elif self.runner.done() and index == self.count:
                self.consumed = True
                return
            else:
                await self.changed.wait()


@asynccontextmanager
async def pcex_schedule(items, transformer, window, *, paths=None, root=None, with_furniture=False, **kwargs):
    """Own all producers until consumers and callbacks have settled."""
    async with temporary_directory('pdf-craft-pcex-ready-') as directory:
        queue = await IO_DOMAIN.run(ReadyQueue, directory / 'work.sqlite')
        furniture = await IO_DOMAIN.run(FurniturePlan, paths, root or directory, queue) if with_furniture else None
        # PCEX's window bounds business jobs across every stream. Each job
        # submits groups serially to the same model runtime/executor, avoiding
        # a second window multiplying capacity inside every admitted job.
        from .narrative_xml import NarrativeXMLTransformer
        if isinstance(transformer, NarrativeXMLTransformer) and transformer.supports_stream:
            transformer = NarrativeXMLTransformer(transformer._translator, transformer.mode, window=1)
        schedule = PCEXSchedule(items, transformer, window, queue, furniture=furniture, **kwargs)
        from .progress import active_progress
        progress = active_progress()
        if progress is not None:
            progress.pending_streams += 1
        runner = asyncio.create_task(schedule.run())
        schedule.runner = runner
        try:
            yield schedule
            # A normal context exit may still mean the consumer stopped early.
            # Do not finish an unread book (or wait for blocked model requests).
            if not schedule.consumed:
                runner.cancel()
            else:
                await runner
        finally:
            runner.cancel()
            try:
                await settle_tasks([runner])
            finally:
                if progress is not None and schedule.consumed:
                    progress.pending_streams -= 1
