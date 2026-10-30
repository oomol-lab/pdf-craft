"""Barrier regressions for local dependencies, bounded work and ordered delivery."""
# pylint: disable=protected-access
import asyncio
from copy import deepcopy
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase



def _raw(index):
    from tests.test_refinement_boundaries import _page
    return _page(index, '')


async def refine_pages(pages, process, *, window):
    # Exercise the production readiness scheduler, including cancellation of
    # its owned scanner/jobs when a consumer closes early.
    from contextlib import aclosing
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from pdf_craft.extractor.refinement import refine_ready
    with TemporaryDirectory() as directory:
        async with aclosing(refine_ready(pages, process, Path(directory), lambda: [], window=window)) as results:
            async for result in results:
                yield result.page


class RefinementPipelineTests(IsolatedAsyncioTestCase):
    async def test_page_results_do_not_wait_for_later_input_or_unrelated_work(self):
        entered, release, delivered = asyncio.Event(), asyncio.Event(), []
        async def source():
            for index in range(1, 7):
                yield _raw(index)
            await release.wait()
        async def process(_previous, target, _following):
            if target.page_index == 2:
                entered.set()
                await release.wait()
            return deepcopy(target)
        async def consume():
            async for result in refine_pages(source(), process, window=3):
                delivered.append(result.page_index)
                if result.page_index == 1:
                    break
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(task, 2)
            self.assertEqual(delivered, [1])
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_neighbors_are_immutable_initial_results_and_work_is_bounded(self):
        release, entered = asyncio.Event(), asyncio.Event()
        reads, calls, results = [], [], []
        async def source():
            for index in range(1, 101):
                reads.append(index)
                yield _raw(index)
        async def process(previous, target, following):
            self.assertFalse(getattr(previous, "candidate_marker", False))
            self.assertFalse(getattr(following, "candidate_marker", False))
            calls.append(target.page_index)
            entered.set()
            await release.wait()
            # Mutating a candidate must not change another job's context.
            target.candidate_marker = True
            return target
        async def consume():
            async for result in refine_pages(source(), process, window=3):
                results.append(result.page_index)
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(0.03)
            self.assertLessEqual(len(calls), 3)
        finally:
            release.set()
            await asyncio.wait_for(task, 5)
        self.assertEqual(results, list(range(1, 101)))

    async def test_real_entry_starts_llm_before_later_ocr_and_neighbor_jev(self):
        await self._check_local_entry(True)

    async def test_real_entry_does_not_wait_for_all_jev_after_ocr_eof(self):
        await self._check_local_entry(False)

    async def test_body_only_entry_keeps_incremental_delivery(self):
        await self._check_local_entry(True, footnotes=False)

    async def _check_local_entry(self, block_ocr, footnotes=True):
        import json
        import threading
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from pdf_craft import AsyncPDFCraft, ExtractionOptions
        from pdf_craft.transform import PDFExtractionEngine
        from pdf_craft.jev import JEVRuntime
        from tests.test_toc_identity import IdentityOCR, refinement
        from tests.test_page_repair import _response_from_message
        loop = asyncio.get_running_loop()
        ocr_blocked, neighbor_jev, llm_started, body_ready = (asyncio.Event() for _ in range(4))
        release_ocr, release_jev = threading.Event(), asyncio.Event()
        prompts = []
        class OCR(IdentityOCR):
            def recognize(self, **kwargs):
                for event in super().recognize(**kwargs):
                    yield event
                    if block_ocr and event.page_index == 4 and event.kind.name == 'COMPLETE':
                        loop.call_soon_threadsafe(ocr_blocked.set)
                        if not release_ocr.wait(10):
                            raise TimeoutError('OCR barrier not released')
        async def evaluate(_runtime, index, _packet, **_kwargs):
            if index == 2:
                neighbor_jev.set()
                await release_jev.wait()
            return 0.1
        class Runtime:
            async def request(self, messages, **_kwargs):
                payload = json.loads(messages[1].message)
                prompts.append(payload)
                if payload['target_page']['page_index'] == 1:
                    llm_started.set()
                return json.dumps(_response_from_message(messages[1]))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine('ignored')
            engine._ocr = cast(Any, OCR())
            with patch.object(JEVRuntime, 'evaluate', evaluate), patch('pdf_craft.transform.runtime_for', return_value=Runtime()):
                task = asyncio.create_task(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=footnotes, refinement=refinement(), on_body=lambda _node: body_ready.set()),
                    analysing_path=root/'analysis', error_policy=False))
                try:
                    if block_ocr:
                        await asyncio.wait_for(ocr_blocked.wait(), 3)
                    await asyncio.wait_for(neighbor_jev.wait(), 2)
                    await asyncio.wait_for(llm_started.wait(), 2)
                    await asyncio.wait_for(body_ready.wait(), 2)
                    self.assertFalse(task.done())
                    first = next(p for p in prompts if p['target_page']['page_index'] == 1)
                    self.assertEqual(first['target_page']['jev_p_pass'], 0.1)
                    for key in ('previous_page', 'next_page'):
                        if first[key] is not None:
                            self.assertNotIn('jev_p_pass', first[key])
                finally:
                    release_ocr.set()
                    release_jev.set()
                    await asyncio.wait_for(task, 10)

    async def test_adopted_cross_page_paragraph_and_note_are_complete_and_emitted_once(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from xml.etree.ElementTree import tostring
        from pdf_craft.extractor.chapter.assembly import PageAssembly
        from pdf_craft.extractor.chapter.page_analysis import analyse_pages
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, Reference, encode
        from pdf_craft.extractor.chapter.stream import chapters_from_disk
        from pdf_craft.extractor.toc import TocInfo
        from tests.test_page_analysis import _fragment
        note = Reference(1, 1, '①', [TextFlowItem('body', -1, [
            _fragment(1, 1, 'Note starts '), _fragment(2, 1, 'and ends.')])])
        pages = analyse_pages([1, 2, 3], [
            TextFlowItem('body', -1, [_fragment(1, 0, 'Body starts ', note), _fragment(2, 0, 'and ends.')]),
            TextFlowItem('body', -1, [_fragment(3, 0, 'Next paragraph.')])], [note])
        with TemporaryDirectory() as directory:
            bodies, notes = [], []
            path = Path(directory)/'work.sqlite'
            assembly = PageAssembly(path, on_body=lambda n: bodies.append(tostring(n)),
                                    on_footnote=lambda n: notes.append(tostring(n)))
            assembly.append(pages[0])
            self.assertFalse(bodies)
            self.assertFalse(notes)
            assembly.append(pages[1])
            self.assertFalse(bodies)
            assembly.append(pages[2])
            self.assertEqual(len(bodies), 1)
            self.assertEqual(len(notes), 1)
            assembly.finish()
            self.assertEqual(len(bodies), 2)
            self.assertEqual(len(notes), 1)
            chapters = list(chapters_from_disk(path, TocInfo([], []), chunk_size=128))
            xml = b''.join(tostring(encode(chapter)) for chapter in chapters)
            self.assertIn(b'Note starts', xml)
            self.assertIn(b'and ends.', xml)
            self.assertEqual(sum(len(chapter.flow_items) for chapter in chapters), 2)
            # Final TOC exclusion affects the artifact, never the delivered stream.
            filtered = list(chapters_from_disk(path, TocInfo([], [3]), chunk_size=128))
            self.assertNotIn(b'Next paragraph', b''.join(tostring(encode(c)) for c in filtered))

    async def test_source_failure_cancels_jobs_and_preserves_terminal_error(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        failure = ValueError('source failed after publishing a prefix')
        async def source():
            yield _raw(1)
            yield _raw(2)
            await entered.wait()
            raise failure
        async def process(_previous, target, _following):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return target
        async def consume():
            async for _ in refine_pages(source(), process, window=2):
                pass
        with self.assertRaises(ValueError) as caught:
            await asyncio.wait_for(consume(), 2)
        self.assertIs(caught.exception, failure)
        self.assertTrue(cancelled.is_set())

    async def test_rejected_page_does_not_emit_or_commit_partial_units(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pdf_craft.extractor.chapter.assembly import PageAssembly
        from pdf_craft.extractor.chapter.page_analysis import analyse_pages, PageStructureError
        from pdf_craft.extractor.chapter.chapter import TextFlowItem
        from tests.test_page_analysis import _fragment
        pages = analyse_pages([1, 2], [
            TextFlowItem('body', -1, [_fragment(1, 0, 'Opening ')]),
            TextFlowItem('body', -1, [_fragment(2, 0, 'Closed first.'), _fragment(2, 1, 'Closed second.')])], [])
        with TemporaryDirectory() as directory:
            delivered = []
            assembly = PageAssembly(Path(directory)/'work.sqlite', on_body=delivered.append)
            assembly.append(pages[0])
            invalid = deepcopy(pages[1])
            invalid.layouts[1].annotations.paragraph_role = 'heading'
            with self.assertRaises(PageStructureError):
                assembly.append(invalid)
            self.assertFalse(delivered)
            self.assertEqual(assembly._run('SELECT page FROM adopted'), [(1,)])
            assembly.append(pages[1])
            assembly.finish()
            self.assertEqual(len(delivered), 2)

    async def test_ocr_backpressure_never_holds_page_consumption_lock(self):
        from contextlib import asynccontextmanager
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from xml.etree.ElementTree import fromstring
        from pdf_craft.common import save_xml
        from pdf_craft.pdf import decode, encode
        from pdf_craft.pdf.ocr import OCREvent, OCREventKind
        from pdf_craft.extractor.chapter.live import LiveExtraction
        entered, release = asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                if target.page_index == 1:
                    entered.set()
                    await release.wait()
                return target
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root/'ocr'
            pages.mkdir()
            for index in range(1, 14):
                save_xml(encode(decode(fromstring(f'<page index="{index}"><body>'
                    f'<layout ref="text" det="1,1,90,20">Page {index}.</layout>'
                    '</body></page>'))), pages/f'page_{index}.xml')
            live = LiveExtraction(root/'live', pages, True, refinement=factory)
            waiting = None
            try:
                for index in range(1, 13):
                    await live.event(OCREvent(OCREventKind.START, index, 13))
                waiting = asyncio.create_task(live.event(OCREvent(OCREventKind.START, 13, 13)))
                await asyncio.sleep(0)
                self.assertFalse(waiting.done())
                # Earlier requests must publish even while the later START waits.
                for index in range(2, 13):
                    await asyncio.wait_for(live.event(OCREvent(OCREventKind.COMPLETE, index, 13)), 2)
                await asyncio.wait_for(live.event(OCREvent(OCREventKind.COMPLETE, 1, 13)), 2)
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(waiting.done())
                release.set()
                await asyncio.wait_for(waiting, 2)
                await live.event(OCREvent(OCREventKind.COMPLETE, 13, 13))
                await asyncio.wait_for(live.finish(), 3)
                self.assertEqual(live._consumed, 13)
            finally:
                release.set()
                if waiting is not None:
                    waiting.cancel()
                    await asyncio.gather(waiting, return_exceptions=True)
                await live.cancel()

    async def test_complete_superscript_reference_can_replace_atomic_ocr_expression(self):
        from pdf_craft.expression import ExpressionKind
        from pdf_craft.extractor.chapter.chapter import InlineExpression, SourceTextFragment, TextFlowItem
        from pdf_craft.extractor.chapter.page_analysis import (
            analyse_pages, replace_layout_references, ReferenceSpan, layout_reference_spans)
        paragraph = TextFlowItem('body', -1, [SourceTextFragment(1, 0, (0, 0, 100, 20),
            ['Text ', InlineExpression(ExpressionKind.INLINE_PAREN, '^{33}'), ' follows.'])])
        layout = analyse_pages([1], [paragraph], [])[0].layouts[0]
        span = ReferenceSpan(5, 10, 1, 1, '^{33}')
        restored = replace_layout_references(layout, [span])
        self.assertEqual(layout_reference_spans(restored), [span])
        # A partial expression remains ambiguous/structurally invalid.
        with self.assertRaises(ValueError):
            replace_layout_references(layout, [ReferenceSpan(7, 9, 1, 1, '33')])

    async def test_local_candidate_fallback_preserves_previously_adopted_repairs(self):
        from contextlib import asynccontextmanager
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from zipfile import ZipFile
        from pdf_craft import AsyncPDFCraft, ExtractionOptions
        from tests.test_toc_identity import RefinedEngine, refinement
        class Engine(RefinedEngine):
            @asynccontextmanager
            async def _stream_refinement(self, options, kwargs):
                async with super()._stream_refinement(options, kwargs) as process:
                    async def repair(previous, target, following):
                        result = await process(previous, target, following)
                        if target.page_index == 4:
                            result.initial = deepcopy(target)
                            layout = result.page.layouts[0]
                            layout.annotations.continues_from_previous = True
                            layout.annotations.paragraph_role = 'heading'
                        return result
                    yield repair
        with TemporaryDirectory() as directory:
            root = Path(directory)
            decisions = []
            await AsyncPDFCraft.from_engine(Engine('ignored')).extract_pdf(
                root/'source.pdf', root/'book.pcex', ExtractionOptions(
                    includes_furniture=False, footnotes=True, refinement=refinement()),
                analysing_path=root/'analysis', error_policy=lambda event: decisions.append(event) or True)
            self.assertEqual([event.stage for event in decisions], ['footnote_commit'])
            with ZipFile(root/'book.pcex') as archive:
                text = b''.join(archive.read(p) for p in archive.namelist() if p.startswith('chapters/'))
            self.assertNotIn(b'Second chapter', text)
            self.assertIn(b'Page 4 body.', text)

    async def test_assembly_failure_settles_siblings_before_provider_closes(self):
        from contextlib import asynccontextmanager
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from xml.etree.ElementTree import fromstring
        from pdf_craft.pdf import decode
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from pdf_craft.extractor.chapter.page_analysis import PageStructureError
        entered, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                if target.page_index == 1:
                    await entered.wait()
                    target.layouts[0].annotations.continues_from_previous = True
                elif target.page_index == 2:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return target
            try:
                yield process
            finally:
                self.assertTrue(cancelled.is_set(), 'provider closed before sibling tasks settled')
                closed.set()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            live = LiveExtraction(root/'live', root, True, refinement=factory)
            try:
                for index in range(1, 4):
                    live.pages.append(decode(fromstring(f'<page index="{index}"><body>'
                        f'<layout ref="text" det="0,0,90,20">Page {index}.</layout>'
                        '</body></page>')))
                live.pages.close()
                with self.assertRaises(PageStructureError):
                    await asyncio.wait_for(live.task, 3)
                self.assertTrue(closed.is_set())
            finally:
                await live.cancel()

    async def test_dependency_closure_includes_transparent_asset_pages(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pdf_craft.extractor.chapter.assembly import PageAssembly
        from pdf_craft.extractor.chapter.page_analysis import analyse_pages
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceAsset
        from tests.test_page_analysis import _fragment
        pages = analyse_pages([1, 2, 3], [TextFlowItem('body', -1, [
            _fragment(1, 0, 'Start '), SourceAsset(2, 'image', (0, 0, 100, 100), asset_hash='a'*64),
            _fragment(3, 0, 'continued.')])], [])
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'work.sqlite')
            for page in pages:
                assembly.append(page)
            edges = assembly._run('SELECT left_page,right_page FROM edges')
            closure = {3}
            while True:
                expanded = closure | {p for edge in edges if closure.intersection(edge) for p in edge}
                if expanded == closure:
                    break
                closure = expanded
            self.assertEqual(closure, {1, 2, 3})

    async def test_ignored_pages_do_not_exhaust_refinement_admission(self):
        from contextlib import asynccontextmanager
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from xml.etree.ElementTree import fromstring
        from pdf_craft.pdf import decode, encode
        from pdf_craft.common import save_xml
        from pdf_craft.pdf.ocr import OCREvent, OCREventKind
        from pdf_craft.extractor.chapter.live import LiveExtraction
        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                return target
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            live = LiveExtraction(root/'live', root, True, refinement=factory)
            live._page_indexes = {30, 31}
            async def feed():
                for index in range(1, 32):
                    await live.event(OCREvent(OCREventKind.START, index, 31))
                    if index < 30:
                        await live.event(OCREvent(OCREventKind.IGNORE, index, 31))
                    else:
                        save_xml(encode(decode(fromstring(f'<page index="{index}"><body>'
                            f'<layout ref="text" det="0,0,90,20">Page {index}.</layout>'
                            '</body></page>'))), root/f'page_{index}.xml')
                        await live.event(OCREvent(OCREventKind.COMPLETE, index, 31))
                await live.finish()
            try:
                await asyncio.wait_for(feed(), 2)
                self.assertEqual(live._consumed, 2)
            finally:
                await live.cancel()
            cached = LiveExtraction(root/'cached-live', root, True, refinement=factory, page_indexes={31})
            try:
                await asyncio.wait_for(cached.finish(), 2)
                self.assertEqual(cached._consumed, 1)
            finally:
                await cached.cancel()
