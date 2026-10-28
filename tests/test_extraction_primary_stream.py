"""Primary extraction notifications must not wait for unrelated whole-book work."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, ExtractionOptions, FootnoteOptions
from tests.test_toc_identity import IdentityOCR, RefinedEngine, refinement


class PrimaryStreamTests(IsolatedAsyncioTestCase):
    async def test_closed_content_arrives_while_later_ocr_is_blocked(self):
        for refined in (False, True):
            with self.subTest(refined=refined), TemporaryDirectory() as directory:
                root = Path(directory)
                blocked, body_ready, note_ready = (asyncio.Event() for _ in range(3))
                release = threading.Event()
                loop = asyncio.get_running_loop()
                bodies, notes = [], []

                class OCR(IdentityOCR):
                    def recognize(self, **kwargs):
                        for event in super().recognize(**kwargs):
                            yield event
                            if event.page_index == 4 and event.kind.name == "COMPLETE":
                                loop.call_soon_threadsafe(blocked.set)
                                if not release.wait(10):
                                    raise TimeoutError('test did not release OCR')

                engine = RefinedEngine('ignored')
                engine._ocr = OCR()
                def body(node):
                    bodies.append(tostring(node))
                    node.clear()  # Public values must be detached from analysis.
                    body_ready.set()
                def note(node):
                    notes.append(tostring(node))
                    node.clear()
                    note_ready.set()
                task = asyncio.create_task(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex',
                    ExtractionOptions(includes_furniture=False,
                                      footnotes=refinement() if refined else FootnoteOptions(),
                                      on_body=body, on_footnote=note),
                    analysing_path=root/'analysis', error_policy=False))
                try:
                    await asyncio.wait_for(blocked.wait(), 5)
                    await asyncio.wait_for(asyncio.gather(body_ready.wait(), note_ready.wait()), 1)
                    self.assertFalse(task.done())
                    self.assertFalse((root/'book.pcex').exists())
                finally:
                    release.set()
                    await task
                self.assertEqual(len(notes), 6)
                self.assertEqual(len(bodies), 7 if refined else 8)
                self.assertEqual(len(set(notes)), 6)
                self.assertEqual([fromstring(node).get("id") for node in notes],
                                 [f"{index}-1" for index in range(1, 7)])
                fragments = [fragment for node in bodies for fragment in fromstring(node).iter("fragment")]
                self.assertTrue(all(fragment.get("bbox") and fragment.get("source_order") is not None
                                    for fragment in fragments))
                self.assertEqual(b'Second chapter' in b''.join(bodies), not refined)
                with ZipFile(root/'book.pcex') as archive:
                    final = b''.join(archive.read(name) for name in archive.namelist() if name.startswith('chapters/'))
                self.assertEqual(b'Second chapter' in final, not refined)

    async def test_cached_content_precedes_metadata_toc_and_final_commit(self):
        for cached in (False, True):
            with self.subTest(cached=cached), TemporaryDirectory() as directory:
                root = Path(directory)
                bodies, notes = [], []
                entered, release = asyncio.Event(), asyncio.Event()
                class OCR(IdentityOCR):
                    def recognize(self, **kwargs):
                        events = super().recognize(**kwargs)
                        if cached:
                            for _ in events:
                                pass  # Persisted cache: no OCR events reach LiveExtraction.
                        else:
                            yield from events
                class Engine(RefinedEngine):
                    async def _resolve_llm_stages(self, *args, **kwargs):
                        entered.set()
                        await release.wait()
                        return await super()._resolve_llm_stages(*args, **kwargs)
                engine = Engine('identity')
                engine._ocr = OCR()
                task = asyncio.create_task(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex',
                    ExtractionOptions(includes_furniture=False, footnotes=FootnoteOptions(),
                                      on_body=lambda node: bodies.append(tostring(node)),
                                      on_footnote=lambda node: notes.append(tostring(node))),
                    analysing_path=root/'analysis', error_policy=False))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    self.assertEqual(len(bodies), 8)
                    self.assertEqual(len(notes), 6)
                    self.assertFalse(task.done())
                finally:
                    release.set()
                    await task
                self.assertEqual(len(bodies), 8, 'no duplicate final replay')
                self.assertEqual(len(notes), 6, 'no duplicate final replay')

    async def test_callback_error_and_cancellation_abort_before_finalization(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), TemporaryDirectory() as directory:
                root = Path(directory)
                entered = asyncio.Event()
                failure = ValueError('primary consumer failed')
                class Engine(RefinedEngine):
                    async def _resolve_llm_stages(self, *args, **kwargs):
                        raise AssertionError('failed delivery must not reach final analysis')
                async def callback(_node):
                    entered.set()
                    if cancel:
                        await asyncio.Event().wait()
                    raise failure
                task = asyncio.create_task(AsyncPDFCraft.from_engine(Engine('identity')).extract_pdf(
                    root/'source.pdf', root/'book.pcex',
                    ExtractionOptions(includes_furniture=False, on_body=callback),
                    analysing_path=root/'analysis', error_policy=True))
                await asyncio.wait_for(entered.wait(), 5)
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                else:
                    with self.assertRaises(ValueError) as raised:
                        await asyncio.wait_for(task, 5)
                    self.assertIs(raised.exception, failure)
                self.assertFalse((root/'book.pcex').exists())
