"""Real extraction entry must not serialize independent OCR neighborhoods."""
import asyncio
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft import AsyncPDFCraft, ExtractionOptions
from pdf_craft.jev import JEVRuntime
from pdf_craft.pdf.ocr import OCREventKind
from pdf_craft.transform import PDFExtractionEngine
from tests.test_page_repair import _response_from_message
from tests.test_toc_identity import IdentityOCR, refinement


class RefinementReadinessTests(IsolatedAsyncioTestCase):
    async def test_third_page_refines_while_first_ocr_is_pending(self):
        release = threading.Event()
        blocked, refined = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        calls, bodies = [], []

        class OCR(IdentityOCR):
            def recognize(self, **kwargs):
                deferred = None
                for event in super().recognize(**kwargs):
                    if event.kind == OCREventKind.COMPLETE and event.page_index == 1:
                        deferred = event
                        continue
                    yield event
                    if event.kind == OCREventKind.COMPLETE and event.page_index == 4:
                        loop.call_soon_threadsafe(blocked.set)
                        if not release.wait(5):
                            raise TimeoutError('test OCR barrier was not released')
                        assert deferred is not None
                        yield deferred

        async def evaluate(_runtime, index, _packet, **_kwargs):
            calls.append(index)
            return 0.1

        class Runtime:
            async def request(self, messages, **_kwargs):
                data = _response_from_message(messages[1])
                if data['page_index'] == 3:
                    refined.set()
                return json.dumps(data)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine('unused')
            engine._ocr = cast(Any, OCR())  # pylint: disable=protected-access
            with patch.object(JEVRuntime, 'evaluate', evaluate), patch(
                    'pdf_craft.transform.runtime_for', return_value=Runtime()):
                task = asyncio.create_task(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=refinement(), on_body=bodies.append),
                    analysing_path=root/'analysis', error_policy=False))
                try:
                    await asyncio.wait_for(blocked.wait(), 3)
                    try:
                        await asyncio.wait_for(refined.wait(), 0.5)
                    except TimeoutError:
                        self.fail('page 3 JEV/LLM waited for unrelated OCR page 1')
                    self.assertIn(3, calls)
                    self.assertNotIn(1, calls)
                    self.assertFalse(bodies, 'natural paragraph delivery must still preserve page order')
                finally:
                    release.set()
                    await asyncio.wait_for(task, 10)
                self.assertEqual(sorted(calls), list(range(1, 7)))
                self.assertTrue(bodies)

    async def test_window_aliases_preserve_long_footnotes_and_stable_prompts(self):
        from contextlib import asynccontextmanager
        from xml.etree.ElementTree import fromstring, tostring
        from pdf_craft.common import save_xml
        from pdf_craft.pdf import decode, encode
        from pdf_craft.pdf.ocr import OCREvent
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from pdf_craft.extractor.chapter.primary import PrimaryPages
        from pdf_craft.extractor.chapter.assembly import PageAssembly
        from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm, build_page_repair_messages
        pages = [decode(fromstring(f'<page index="{i}"><body>'
                 f'<layout ref="text" det="1,1,90,20">Body {i}.</layout></body><footnotes>'
                 f'<layout ref="text" det="1,90,90,105">{note}</layout></footnotes></page>'))
                 for i, note in enumerate(('① Note starts', 'and continues', 'yet continues', 'and ends.'), 1)]
        runs = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = []
            assembly = PageAssembly(root/'baseline.sqlite', on_footnote=lambda n: baseline.append(tostring(n)))
            initial = PrimaryPages()
            for page in pages:
                save_xml(encode(page), root/f'page_{page.index}.xml')
                assembly.append(initial.append(page))
            assembly.finish()
            self.assertEqual(len(baseline), 1)
            for order in ((1, 2, 3, 4), (4, 2, 3, 1)):
                notes, prompts, entered = [], {}, asyncio.Event()
                # Bind this run's observation sinks; the factory never escapes
                # this iteration and does not share a default across runs.
                @asynccontextmanager
                async def factory(prompts=prompts, entered=entered):  # pylint: disable=dangerous-default-value
                    async def process(previous, target, following):
                        prompts[target.page_index] = build_page_repair_messages(
                            previous_page=previous, target_page=target, next_page=following,
                            previous_pass_probability=None, next_pass_probability=None)[1].message
                        if target.page_index == 3:
                            entered.set()
                        async def request(messages, _index, _maximum):
                            return json.dumps(_response_from_message(messages[1]))
                        return await repair_page_with_llm(
                            previous_page=previous, target_page=target, next_page=following,
                            previous_pass_probability=None, next_pass_probability=None, request=request)
                    yield process
                live = LiveExtraction(root/f'live-{order[0]}', root, True, refinement=factory,
                                      on_footnote=lambda n, notes=notes: notes.append(tostring(n)))
                try:
                    for i in range(1, 5):
                        await live.event(OCREvent(OCREventKind.START, i, 4))
                    for i in order:
                        await live.event(OCREvent(OCREventKind.COMPLETE, i, 4))
                        # Duplicate terminal notification must not enqueue the
                        # same raw page or change an in-flight snapshot.
                        await live.event(OCREvent(OCREventKind.COMPLETE, i, 4))
                        if i == 3 and order[0] == 4:
                            await asyncio.wait_for(entered.wait(), 2)
                            self.assertFalse(notes)
                    await asyncio.wait_for(live.finish(), 3)
                    self.assertEqual(notes, baseline)
                    self.assertEqual(live._consumed, 4)  # pylint: disable=protected-access
                    runs.append(prompts)
                finally:
                    await live.cancel()
            self.assertEqual(runs[0], runs[1])

    async def test_completed_later_jobs_do_not_hold_slots_until_ordered_delivery(self):
        from contextlib import aclosing
        from pdf_craft.extractor.refinement import refine_ready
        from tests.test_refinement_boundaries import _page, _text
        release, later = asyncio.Event(), asyncio.Event()
        active, maximum, delivered = 0, 0, []
        async def source():
            for index in range(1, 7):
                yield _page(index, _text(f'Page {index}.'))
        async def process(_previous, target, _following):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                if target.page_index == 1:
                    await release.wait()
                if target.page_index == 5:
                    later.set()
                return target
            finally:
                active -= 1
        with TemporaryDirectory() as directory:
            async def consume():
                async with aclosing(refine_ready(source(), process, Path(directory), lambda: [], window=2)) as results:
                    async for result in results:
                        delivered.append(result.page.page_index)
            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(later.wait(), 2)
                self.assertFalse(delivered)
                self.assertLessEqual(maximum, 2)
            finally:
                release.set()
                await asyncio.wait_for(task, 3)
            self.assertEqual(delivered, list(range(1, 7)))

    async def test_selected_last_page_does_not_wait_for_unselected_tail_or_eof(self):
        from contextlib import asynccontextmanager
        from pdf_craft.common import save_xml
        from pdf_craft.pdf import encode
        from pdf_craft.pdf.ocr import OCREvent
        from pdf_craft.extractor.chapter.live import LiveExtraction
        from tests.test_refinement_boundaries import _page, _text
        @asynccontextmanager
        async def factory():
            async def process(previous, target, following):
                self.assertIsNone(previous)
                self.assertIsNone(following)
                return target
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(_page(3, _text('Selected page.'))), root/'page_3.xml')
            delivered = asyncio.Event()
            live = LiveExtraction(root/'live', root, True, refinement=factory,
                                  page_indexes={3}, on_body=lambda _node: delivered.set())
            try:
                await live.event(OCREvent(OCREventKind.START, 3, 100))
                await live.event(OCREvent(OCREventKind.COMPLETE, 3, 100))
                await asyncio.wait_for(delivered.wait(), 2)
                self.assertEqual(live._consumed, 1)  # pylint: disable=protected-access
                await live.finish()
            finally:
                await live.cancel()

    async def test_corrupt_raw_page_keeps_original_error_and_location(self):
        from contextlib import asynccontextmanager
        from pdf_craft.pdf.ocr import OCREvent
        from pdf_craft.extractor.chapter.live import LiveExtraction
        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                self.fail('corrupt input must not reach the provider')
                return target
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'page_1.xml').write_text('<broken', encoding='utf-8')
            live = LiveExtraction(root/'live', root, True, refinement=factory)
            try:
                await live.event(OCREvent(OCREventKind.START, 1, 3))
                await live.event(OCREvent(OCREventKind.COMPLETE, 1, 3))
                failure = live.analysis_error
                self.assertIsNotNone(failure)
                await live.finish()
                self.assertIs(live.analysis_error, failure)
                self.assertEqual(getattr(live.analysis_error, 'page_index'), 1)
            finally:
                await live.cancel()
