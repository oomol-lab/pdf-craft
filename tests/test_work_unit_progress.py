"""Public lifecycle facts survive recovery, caches and output failures."""
# pylint: disable=protected-access
import asyncio
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft import (
    ExtractionOptions, ProgressEventKind as Kind, UnitKind, UnitOutcome,
    ErrorCallbackError,
)
from pdf_craft._progress import OperationProgress
from pdf_craft.progress import OperationKind
from pdf_craft.document.package import PDFCraftExtraction
from tests import test_failure_fallback_epub as fixtures


def unit_events(events, kind):
    return [event for event in events if event.kind == kind and event.unit_id is not None]


def assert_lifecycle(test, events):
    test.assertEqual(events[0].kind, Kind.STARTED)
    test.assertIsNone(events[0].unit_id)
    starts = unit_events(events, Kind.STARTED)
    ends = unit_events(events, Kind.COMPLETED)
    test.assertEqual([event.unit_id for event in starts], list(range(len(starts))))
    test.assertEqual(Counter(event.unit_id for event in starts), Counter(event.unit_id for event in ends))
    test.assertEqual(Counter(event.unit_kind for event in starts), +Counter(events[0].unit_counts))
    test.assertEqual(events[-1].kind, Kind.COMPLETED)
    test.assertIsNone(events[-1].unit_id)
    for start in starts:
        stages = [event.stage for event in events if event.unit_id == start.unit_id and event.stage is not None]
        test.assertEqual(stages, sorted(stages))
        test.assertEqual(stages[-1], start.max_stage)


class ExtractionUnitTests(TestCase):
    def craft(self, outcomes=None):
        return fixtures.TestFailureFallbackEPUB._craft(
            {i: fixtures.TestFailureFallbackEPUB._page((20, 30, 40)) for i in (1, 2, 3)},
            outcomes or {i: f'Page {i} is readable.' for i in (1, 2, 3)})

    def run_extract(self, craft, root, events, *, name='book', selected=None, **kwargs):
        return craft.extract_pdf('scripted.pdf', root / f'{name}.pcex',
            ExtractionOptions(page_indexes=selected, includes_furniture=False, on_extraction_event=events.append),
            analysing_path=root / 'analysis', **kwargs)

    def test_selected_pages_full_cache_and_partial_cache_have_identical_counts(self):
        with TemporaryDirectory() as folder:
            root, craft = Path(folder), self.craft()
            for index in range(3):
                events = []
                if index == 2:
                    (root / 'analysis/ocr/done').unlink()
                    (root / 'analysis/ocr/page_2.xml').unlink()
                self.run_extract(craft, root, events, name=str(index))
                assert_lifecycle(self, events)
                self.assertEqual(events[0].unit_counts[UnitKind.PAGE], 3)
                pages = [event for event in unit_events(events, Kind.COMPLETED) if event.unit_kind == UnitKind.PAGE]
                self.assertEqual({event.page_index for event in pages}, {1, 2, 3})
                if index == 1:
                    self.assertTrue(all(event.outcome == UnitOutcome.REUSED for event in pages))
                    self.assertFalse(any(event.unit_kind == UnitKind.PAGE and event.stage in (1, 2, 3)
                                         for event in events))
                    self.assertEqual({event.stage for event in events if event.unit_kind == UnitKind.PAGE}, {0, 4, 7})

    def test_ignored_ocr_failure_completes_page_as_fallback(self):
        with TemporaryDirectory() as folder:
            events = []
            self.run_extract(self.craft({1: fixtures.OCRError('bad OCR', 1, 1), 2: 'Good page.', 3: 'Another page.'}),
                             Path(folder), events, error_policy=True)
            assert_lifecycle(self, events)
            page = next(event for event in unit_events(events, Kind.COMPLETED) if event.page_index == 1)
            self.assertEqual(page.outcome, UnitOutcome.FALLBACK)

    def test_export_failure_keeps_completed_page_work_but_not_operation(self):
        with TemporaryDirectory() as folder:
            events = []
            with patch.object(PDFCraftExtraction, '_export_async', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    self.run_extract(self.craft(), Path(folder), events)
            self.assertFalse(any(event.unit_id is None and event.kind == Kind.COMPLETED for event in events))
            self.assertEqual(sum(event.unit_kind == UnitKind.PAGE for event in unit_events(events, Kind.COMPLETED)), 3)

    def test_page_completion_precedes_global_stages(self):
        from pdf_craft.transform import PDFExtractionEngine
        original = PDFExtractionEngine._resolve_llm_stages
        events = []
        async def resolve(engine, *args, **kwargs):
            completed = [e.page_index for e in unit_events(events, Kind.COMPLETED)
                         if e.unit_kind == UnitKind.PAGE]
            self.assertEqual(sorted(completed), [1, 2, 3])
            return await original(engine, *args, **kwargs)
        with TemporaryDirectory() as folder, patch.object(PDFExtractionEngine, '_resolve_llm_stages', resolve):
            self.run_extract(self.craft(), Path(folder), events)

    def test_callback_failure_is_never_ignored(self):
        with TemporaryDirectory() as folder:
            events = []
            def callback(event):
                events.append(event)
                if event.unit_kind == UnitKind.PAGE:
                    raise ValueError('consumer failed')
            with self.assertRaises(ErrorCallbackError):
                self.craft().extract_pdf('scripted.pdf', Path(folder) / 'book.pcex',
                    ExtractionOptions(includes_furniture=False, on_extraction_event=callback), error_policy=True)
            self.assertFalse(any(event.kind == Kind.COMPLETED and
                                 event.unit_kind != UnitKind.METADATA for event in events))

    def test_late_artifact_recovery_does_not_reopen_page_work(self):
        from pdf_craft.extractor.chapter.page_analysis import PageStructureError
        from pdf_craft.transform import PDFExtractionEngine
        original = PDFExtractionEngine._finish_extraction
        failed = False
        def finish(engine, draft, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise PageStructureError('broken final graph', 2)
            return original(engine, draft, **kwargs)
        with TemporaryDirectory() as folder:
            events = []
            with patch.object(PDFExtractionEngine, '_finish_extraction', finish):
                self.run_extract(self.craft(), Path(folder), events, error_policy=True)
            assert_lifecycle(self, events)
            recovered = [event.page_index for event in unit_events(events, Kind.COMPLETED)
                         if event.unit_kind == UnitKind.PAGE and event.outcome == UnitOutcome.FALLBACK]
            self.assertEqual(recovered, [])


class AccountingTests(IsolatedAsyncioTestCase):
    async def test_no_completed_unit_registry_and_interleaved_lifetimes(self):
        events = []
        progress = OperationProgress(events.append, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 1000}, 1000)
        for _ in range(500):
            first, second = await asyncio.gather(
                progress.unit(UnitKind.CONTENT, words=1), progress.unit(UnitKind.CONTENT, words=1))
            await second.finish(UnitOutcome.SKIPPED)
            await first.finish(UnitOutcome.FALLBACK)
        await progress.finish()
        assert_lifecycle(self, events)
        self.assertEqual(events[-1].completed_words, 1000)
        self.assertEqual(set(progress.__dict__), {'callback', 'operation', 'counts', 'finished',
                                                  'next_id', 'total_words', 'completed_words', 'started', 'callback_lock'})
        self.assertLessEqual(len(progress.finished), len(UnitKind))


class RefinementProgressTests(TestCase):
    def test_conditional_refinement_and_ignored_repair_are_settled(self):
        from pdf_craft import Refinement, JEV, LLM, ConcurrentExecutor, FixedCapacity
        from pdf_craft.jev import JEVRuntime
        async def reviews(_runtime, index, _request):
            return 0.0 if index == 1 else 1.0
        with TemporaryDirectory() as folder:
            events = []
            craft = fixtures.TestFailureFallbackEPUB._craft(
                {i: fixtures.TestFailureFallbackEPUB._page((20, 30, 40)) for i in (1, 2, 3)},
                {i: f'Page {i} text.' for i in (1, 2, 3)})
            refinement = Refinement(
                JEV(key='unused'), LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                executor=ConcurrentExecutor(FixedCapacity(2)))
            with patch.object(JEVRuntime, 'evaluate', reviews), patch(
                    'pdf_craft.extractor.chapter.page_repair.repair_page_with_llm', side_effect=ValueError('final repair failure')):
                craft.extract_pdf('scripted.pdf', Path(folder) / 'book.pcex',
                    ExtractionOptions(includes_furniture=False, footnotes=True, refinement=refinement,
                                      on_extraction_event=events.append), error_policy=True)
            assert_lifecycle(self, events)
            self.assertTrue(any(event.page_index == 1 and event.stage == 6 for event in events))
            self.assertFalse(any(event.page_index == 2 and event.stage == 6 for event in events))
            page = next(event for event in unit_events(events, Kind.COMPLETED) if event.page_index == 1)
            self.assertEqual(page.outcome, UnitOutcome.FALLBACK)


class DynamicTranslationTests(IsolatedAsyncioTestCase):
    async def test_dynamic_stream_requires_counts_and_uses_lazy_integer_units(self):
        from xml.etree.ElementTree import fromstring
        from pdf_craft import XMLTranslator, SubmitKind
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        from tests.test_translation_word_progress import translator, identity_fill
        calls, events = [], []
        async def stream():
            for index in range(3):
                yield TranslationTask(fromstring('<body><p>hello world</p></body>'), SubmitKind.REPLACE,
                                      index, item_kind=UnitKind.CONTENT, word_count=2)
        async def request(_self, text):
            calls.append(text)
            return text
        with patch.object(XMLTranslator, '_translate_text_async', request), patch.object(
                XMLTranslator, '_request_and_submit_async', identity_fill):
            with self.assertRaisesRegex(ValueError, 'unit_counts'):
                await translator().translate_elements(stream(), on_translation_event=events.append)
            self.assertEqual(calls, [])
            self.assertEqual(events, [])
            await translator().translate_elements(stream(), on_translation_event=events.append,
                unit_counts={UnitKind.CONTENT: 3}, total_words=6)
        assert_lifecycle(self, events)
        self.assertEqual(events[-1].completed_words, 6)


class MetadataProgressTests(TestCase):
    def test_metadata_fallback_is_one_unit_and_does_not_degrade_its_evidence_pages(self):
        from pdf_craft import LLM
        with TemporaryDirectory() as folder:
            events = []
            craft = fixtures.TestFailureFallbackEPUB._craft(
                {i: fixtures.TestFailureFallbackEPUB._page((20, 30, 40)) for i in (1, 2)},
                {1: 'A title page.', 2: 'A normal body.'})
            with patch('pdf_craft.transform.extract_book_metadata_from_ocr', side_effect=ValueError('invalid metadata')):
                craft.extract_pdf('scripted.pdf', Path(folder) / 'book.pcex',
                    ExtractionOptions(includes_furniture=False, extract_book_metadata=True,
                        metadata_llm=LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
                        on_extraction_event=events.append), error_policy=True)
            assert_lifecycle(self, events)
            self.assertEqual(events[0].unit_counts[UnitKind.METADATA], 1)
            ends = unit_events(events, Kind.COMPLETED)
            self.assertEqual(next(event.outcome for event in ends if event.unit_kind == UnitKind.METADATA),
                             UnitOutcome.FALLBACK)
            self.assertTrue(all(event.outcome == UnitOutcome.PROCESSED for event in ends if event.unit_kind == UnitKind.PAGE))
