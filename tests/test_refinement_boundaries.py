"""Regressions through raw OCR input and the live refinement lifecycle."""
# pylint: disable=protected-access
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.generation import _resolve_pages
from pdf_craft.extractor.chapter.live import LiveExtraction
from pdf_craft.extractor.chapter.page_analysis import analyse_pages
from pdf_craft.extractor.refinement import MAX_PAGE_JOBS
from pdf_craft.pdf import decode, encode


def _page(index, content):
    return decode(fromstring(f'<page index="{index}"><body>{content}</body></page>'))


def _text(text):
    return f'<layout ref="text" det="0,0,90,20">{text}</layout>'


def _asset(kind):
    return f'<layout ref="{kind}" det="0,0,90,20" hash="{"a" * 64}"/>'


class RefinementBoundaryTests(IsolatedAsyncioTestCase):
    async def test_full_window_teardown_cancels_jobs_before_ocr_eof(self):
        for fail in (False, True):
            with self.subTest(ocr_failure=fail):
                await self._check_full_window_teardown(fail)

    async def _check_full_window_teardown(self, fail):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            entered, stop, closed = (asyncio.Event() for _ in range(3))
            active = set()
            started = []
            failure = ValueError('OCR failed while refinement window was full')

            @asynccontextmanager
            async def factory():
                async def process(_previous, target, _following):
                    active.add(target.page_index)
                    started.append(target.page_index)
                    if len(active) == MAX_PAGE_JOBS:
                        entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        active.remove(target.page_index)
                try:
                    yield process
                finally:
                    self.assertFalse(active, 'provider closed before page jobs settled')
                    closed.set()

            live = LiveExtraction(root/'live', root, True, refinement=factory)
            # Input is intentionally not closed: production is still inside OCR.
            for index in range(1, MAX_PAGE_JOBS + 5):
                live.pages.append(_page(index, _text(f'Page {index}.')))

            async def operation():
                try:
                    await stop.wait()
                    raise failure
                finally:
                    await live.cancel()

            task = asyncio.create_task(operation())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if fail:
                    stop.set()
                else:
                    task.cancel('external cancellation')
                done, _ = await asyncio.wait([task], timeout=1)
                self.assertTrue(done, 'teardown waited indefinitely for blocked page jobs')
                with self.assertRaises(ValueError if fail else asyncio.CancelledError) as caught:
                    await task
                if fail:
                    self.assertIs(caught.exception, failure)
                else:
                    self.assertEqual(caught.exception.args, ('external cancellation',))
                self.assertTrue(live.task.done())
                self.assertTrue(closed.is_set())
                self.assertFalse(active)
                self.assertEqual(len(started), MAX_PAGE_JOBS)
            finally:
                # The failing implementation needs this forced cancellation
                # so a red regression itself cannot leave the test hung.
                live.task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await live.cancel()

    async def test_raw_primary_preserves_paragraphs_across_asset_pages(self):
        for middle in (['image'], ['table'], ['image', 'table', 'image']):
            with self.subTest(middle=middle):
                await self._check_raw_path(middle, 'The sentence begins', True)

    async def test_raw_primary_keeps_real_paragraph_boundaries(self):
        for middle, start in ((['equation'], 'The sentence begins'),
                              (['image', 'equation', 'table'], 'The sentence begins'),
                              ([''], 'The sentence begins'),
                              (['image', 'table'], 'A complete sentence.')):
            with self.subTest(middle=middle, start=start):
                await self._check_raw_path(middle, start, False)

    async def test_primary_tail_does_not_retain_intervening_asset_pages(self):
        import weakref
        from pdf_craft.extractor.chapter.primary import PrimaryPages
        initial = PrimaryPages()
        initial.append(_page(1, _text('The sentence begins')))
        references = []
        for index in range(2, 102):
            page = _page(index, _asset('table' if index % 2 else 'image'))
            references.append(weakref.ref(page))
            initial.append(page)
        # Only the current physical page and the earlier text boundary survive.
        self.assertEqual(sum(ref() is not None for ref in references), 1)
        last = initial.append(_page(102, _text('and ends here.')))
        self.assertTrue(last.layouts[0].continues_from_previous)

    async def _check_raw_path(self, middle, start, merged):
        pages = [_page(1, _text(start))]
        pages.extend(_page(i + 2, _asset(kind) if kind else '') for i, kind in enumerate(middle))
        pages.append(_page(len(pages) + 1, _text('and ends here.')))
        original = [tostring(encode(page)) for page in pages]
        paragraphs, citations = _resolve_pages(deepcopy(pages))
        expected = analyse_pages([page.index for page in pages], paragraphs, citations)
        observed = []

        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                observed.append(deepcopy(target))
                # This is the actual no-LLM/JEV-accepted path, not a hand-built
                # PageAnalysis that already has the correct continuation flag.
                return target
            yield process

        with TemporaryDirectory() as directory:
            root = Path(directory)
            bodies, baseline = [], []
            assembly = PageAssembly(root/'baseline.sqlite', on_body=lambda node: baseline.append(tostring(node)))
            for page in expected:
                assembly.append(page)
            assembly.finish()
            live = LiveExtraction(root/'live', root, True, refinement=factory,
                                  on_body=lambda node: bodies.append(tostring(node)))
            try:
                for page in pages:
                    live.pages.append(page)
                live.pages.close()
                await asyncio.wait_for(live.finish(), 3)
                last = next(page for page in observed if page.page_index == len(pages))
                self.assertEqual(last.layouts[0].continues_from_previous, merged)
                self.assertEqual(bodies, baseline)
                import sqlite3
                from contextlib import closing
                with closing(sqlite3.connect(live.database)) as db:
                    edges = db.execute('SELECT left_page,right_page FROM edges').fetchall()
                self.assertEqual(edges, assembly._run('SELECT left_page,right_page FROM edges'))
                if merged:
                    self.assertEqual(len(bodies), 1)
                    self.assertEqual({p for edge in edges for p in edge}, set(range(1, len(pages) + 1)))
                self.assertEqual([tostring(encode(page)) for page in pages], original)
            finally:
                await live.cancel()
