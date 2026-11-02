"""Stage-local refinement facts through the public extraction callback."""
# pylint: disable=protected-access
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch

from pdf_craft import (
    ConcurrentExecutor, ErrorCallbackError, ExtractionOptions, FixedCapacity,
    JEV, LLM, ProgressEventKind, Refinement, RefinementStatus, UnitKind,
)
from pdf_craft.extractor.chapter.analysis_types import PageStructureError
from pdf_craft.transform import PDFExtractionEngine
from tests import test_failure_fallback_epub as fixtures


class RefinementStatusTests(TestCase):
    def craft(self):
        return fixtures.TestFailureFallbackEPUB._craft({i: fixtures.TestFailureFallbackEPUB._page((20, 30, 40)) for i in (1, 2, 3)},
                               {i: f'Page {i} text.' for i in (1, 2, 3)})

    def options(self, callback, footnotes, enabled=True):
        refinement = Refinement(
            JEV(key='unused'), LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
            executor=ConcurrentExecutor(FixedCapacity(2))) if enabled else None
        return ExtractionOptions(includes_furniture=False, footnotes=footnotes,
                                 refinement=refinement, on_extraction_event=callback)

    def completed(self, events):
        return [event for event in events if event.unit_kind == UnitKind.PAGE
                and event.kind == ProgressEventKind.COMPLETED]

    def assert_statuses(self, events, status):
        completed = self.completed(events)
        self.assertEqual(sorted(event.page_index for event in completed), [1, 2, 3])
        self.assertTrue(all(event.refinement_status == status for event in completed))
        self.assertTrue(all(event.refinement_status is None for event in events
                            if event not in completed))

    def test_local_routes_report_status_in_both_footnote_modes(self):
        scenarios = (
            ('pass', 1.0, False, RefinementStatus.NOT_REQUIRED),
            ('nothing-to-review', None, False, RefinementStatus.NOT_REQUIRED),
            ('valid-unchanged-repair', 0.0, False, RefinementStatus.APPLIED),
            ('invalid-repair', 0.0, True, RefinementStatus.FALLBACK),
            ('failed-review', ValueError('review unavailable'), False, RefinementStatus.FALLBACK),
        )
        for footnotes in (False, True):
            for name, probability, repair_failed, expected in scenarios:
                with self.subTest(footnotes=footnotes, route=name), TemporaryDirectory() as directory:
                    events = []
                    async def repair(*, target_page, failed=repair_failed, **_kwargs):
                        if failed:
                            raise ValueError('invalid candidate')
                        # A valid no-op still completed LLM refinement. Text
                        # differences cannot determine whether it was applied.
                        return deepcopy(target_page)
                    module = 'footnote_review.review_footnote_page' if footnotes else 'structural_review.review_body_page'
                    with patch('pdf_craft.extractor.chapter.' + module,
                               side_effect=probability if isinstance(probability, Exception) else None,
                               return_value=probability), patch(
                            'pdf_craft.extractor.chapter.page_repair.repair_page_with_llm', side_effect=repair) as called:
                        self.craft().extract_pdf('scripted.pdf', Path(directory) / 'book.pcex',
                            self.options(events.append, footnotes), error_policy=True)
                    self.assert_statuses(events, expected)
                    self.assertEqual(called.call_count, 3 if probability == 0.0 else 0)

    def test_disabled_and_cached_pages_report_disabled(self):
        with TemporaryDirectory() as directory:
            root, craft = Path(directory), self.craft()
            for index in range(2):
                events = []
                craft.extract_pdf('scripted.pdf', root / f'book-{index}.pcex',
                    self.options(events.append, False, enabled=False), analysing_path=root / 'analysis')
                self.assert_statuses(events, RefinementStatus.DISABLED)

    def test_ocr_cache_does_not_skip_refinement_status(self):
        with TemporaryDirectory() as directory:
            root, craft = Path(directory), self.craft()
            craft.extract_pdf('scripted.pdf', root / 'initial.pcex',
                self.options(None, False, enabled=False), analysing_path=root / 'analysis')
            events = []
            with patch('pdf_craft.extractor.chapter.structural_review.review_body_page', return_value=1.0):
                craft.extract_pdf('scripted.pdf', root / 'reviewed.pcex',
                    self.options(events.append, False), analysing_path=root / 'analysis')
            self.assert_statuses(events, RefinementStatus.NOT_REQUIRED)

    def test_later_recovery_does_not_rewrite_or_repeat_completed_page_status(self):
        events, failed = [], False
        original = PDFExtractionEngine._finish_extraction
        def finish(engine, draft, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise PageStructureError('late assembly recovery', 2)
            return original(engine, draft, **kwargs)
        async def repair(*, target_page, **_kwargs):
            return deepcopy(target_page)
        with TemporaryDirectory() as directory, patch.object(PDFExtractionEngine, '_finish_extraction', finish), patch(
                'pdf_craft.extractor.chapter.structural_review.review_body_page', return_value=0.0), patch(
                'pdf_craft.extractor.chapter.page_repair.repair_page_with_llm', side_effect=repair):
            self.craft().extract_pdf('scripted.pdf', Path(directory) / 'book.pcex',
                self.options(events.append, False), error_policy=True)
        self.assert_statuses(events, RefinementStatus.APPLIED)

    def test_strict_review_error_does_not_invent_page_completion(self):
        events = []
        with TemporaryDirectory() as directory, patch(
                'pdf_craft.extractor.chapter.structural_review.review_body_page',
                side_effect=ValueError('review unavailable')):
            with self.assertRaisesRegex(ValueError, 'review unavailable'):
                self.craft().extract_pdf('scripted.pdf', Path(directory) / 'book.pcex',
                    self.options(events.append, False), error_policy=False)
        self.assertFalse(self.completed(events))

    def test_status_callback_failure_is_fatal_even_when_recovery_is_allowed(self):
        events = []
        def callback(event):
            events.append(event)
            if event.refinement_status is not None:
                raise ValueError('consumer failed')
        with TemporaryDirectory() as directory:
            with self.assertRaises(ErrorCallbackError):
                self.craft().extract_pdf('scripted.pdf', Path(directory) / 'book.pcex',
                    self.options(callback, False, enabled=False), error_policy=True)
        self.assertFalse(any(event.kind == ProgressEventKind.COMPLETED and event.unit_id is None
                             for event in events))

    def test_ocr_fallback_with_refinement_disabled_has_a_separate_status(self):
        events = []
        craft = fixtures.TestFailureFallbackEPUB._craft(
            {i: fixtures.TestFailureFallbackEPUB._page((20, 30, 40)) for i in (1, 2, 3)},
            {1: fixtures.OCRError('bad OCR', 1, 1), 2: 'Good page.', 3: 'Another page.'})
        with TemporaryDirectory() as directory:
            craft.extract_pdf('scripted.pdf', Path(directory) / 'book.pcex',
                self.options(events.append, False, enabled=False), error_policy=True)
        self.assert_statuses(events, RefinementStatus.DISABLED)
        from pdf_craft import UnitOutcome
        self.assertEqual(next(event for event in self.completed(events) if event.page_index == 1).outcome,
                         UnitOutcome.FALLBACK)


class RefinementStatusTimingTests(IsolatedAsyncioTestCase):
    async def test_status_precedes_unrelated_work_and_cancellation_does_not_fake_results(self):
        import asyncio
        from contextlib import aclosing
        from pdf_craft.extractor.progress import ExtractionProgress, _active
        from pdf_craft.extractor.refinement import PageResult, refine_ready
        from tests.test_refinement_pipeline import _raw
        events, completed = [], asyncio.Event()
        def callback(event):
            events.append(event)
            if event.refinement_status is not None:
                completed.set()
        async def source():
            for index in (1, 2, 3):
                yield _raw(index)
            await asyncio.Event().wait()  # OCR has not reached EOF.
        async def process(_previous, target, _following):
            if target.page_index != 1:
                await asyncio.Event().wait()  # Other page jobs remain pending.
            return PageResult(target, refinement_status=RefinementStatus.APPLIED)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            progress = ExtractionProgress(callback, root / 'units.sqlite', 3, None, refinement_enabled=True)
            token = _active.set(progress)
            async def consume():
                async with aclosing(refine_ready(source(), process, root / 'refinement', lambda: [1, 2, 3])) as results:
                    async for _result in results:
                        pass
            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(completed.wait(), 2)
                terminal = [event for event in events if event.refinement_status is not None]
                self.assertEqual([(event.page_index, event.refinement_status) for event in terminal],
                                 [(1, RefinementStatus.APPLIED)])
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                _active.reset(token)
            self.assertEqual(sum(event.refinement_status is not None for event in events), 1)
