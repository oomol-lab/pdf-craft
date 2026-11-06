"""Explicit JEV input rejection retains the existing bounded repair route."""
# pylint: disable=protected-access,cell-var-from-loop
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

import httpx2
from typesafe_sdk import TypeSafeAPIError, TypeSafeBadRequestError

from pdf_craft import AsyncPDFCraft, ExtractionOptions, NonContinuableError, OperationError, RetryPolicy
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.page_analysis import PageStructureError
from pdf_craft.extractor.chapter.page_review import (
    IncompletePageReview, JevReviewProcessor, build_jev_review_states, review_page,
)
from pdf_craft.jev import JEVInputLimitExceeded, JEVRuntime
from pdf_craft.progress import ProgressEventKind, RefinementStatus, UnitKind
from pdf_craft.request_events import RequestOutcome
from pdf_craft.transform import PDFExtractionEngine
from pdf_craft.usage import UsageCollector, observe_tokens, request_attempt
from tests.test_local_jev_review import inputs
from tests.test_page_repair import _response_from_message
from tests.test_toc_identity import IdentityOCR, refinement


def input_limit():
    return TypeSafeBadRequestError(400, {'error_type': 'max_tokens_exceeded'}, httpx2.Headers(),
                                   endpoint='POST https://provider.invalid/v1/systemone')


def client_with(invoke):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.system_one = AsyncMock(side_effect=invoke)
    return client


class JEVInputLimitTests(IsolatedAsyncioTestCase):
    async def test_first_and_later_round_reach_real_repair_and_adoption(self):
        for footnotes, round_number in ((False, 1), (True, 1), (True, 2)):
            for failure in (None, 'request', 'validation', 'commit'):
                with self.subTest(footnotes=footnotes, round_number=round_number, failure=failure):
                    await self._extract(footnotes, round_number, failure)

    async def _extract(self, footnotes, round_number, failure, *, enabled=True):
        calls, prompts, events, observations, decisions = Counter(), [], [], [], []
        options = refinement()
        options = replace(options, jev=replace(options.jev, on_request=observations.append))
        usage = UsageCollector()

        async def system_one(*, state, questions):
            index = state['local_raw_evidence']['target_page']['page_index']
            calls[index] += 1
            if index == 1 and calls[index] == round_number:
                raise input_limit()
            return SimpleNamespace(nouls={name: SimpleNamespace(noul=.5) for name in questions},
                                   usage={'input_tokens': 10, 'output_tokens': 2})

        async def request(messages, **_kwargs):
            target = json.loads(messages[1].message)['target_page']
            index = target['page_index']
            prompts.append(target)
            with request_attempt(options.llm) as attempt:
                observe_tokens({'input_tokens': 20, 'output_tokens': 3})
                if index == 1 and failure == 'request':
                    raise OperationError('model request failed')
                result = _response_from_message(messages[1])
                if index == 1:
                    if failure == 'validation':
                        result['layouts'].pop()
                    else:
                        result['layouts'][1].update(ownership='ignored', paragraph_role=None, paragraph_level=None)
                if attempt is not None:
                    attempt.accept()
                return json.dumps(result)

        validate = PageAssembly.validate
        def validate_candidate(assembly, page, following=None, *, refined=False, initial=None):
            if page.page_index == 1 and refined and failure == 'commit':
                raise PageStructureError('candidate rejected by assembly', 1)
            return validate(assembly, page, following, refined=refined, initial=initial)

        client = client_with(system_one)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            engine = PDFExtractionEngine('unused')
            ocr = cast(Any, IdentityOCR())
            ocr._get_pdf_handler = lambda: None
            engine._ocr = ocr
            with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client), patch(
                    'pdf_craft.transform.runtime_for', return_value=SimpleNamespace(request=request)), patch.object(
                    PageAssembly, 'validate', validate_candidate), patch(
                    'pdf_craft.pdf.page_ref.pdf_pages_count', return_value=6):
                await AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=footnotes, refinement=options if enabled else None,
                        on_extraction_event=events.append), analysing_path=root/'analysis',
                    retry_policy=RetryPolicy(analysis=0 if failure else 2), usage=usage,
                    error_policy=(lambda decision: decisions.append(decision) or True) if failure else False)
            with ZipFile(root/'book.pcex') as archive:
                chapters = [fromstring(archive.read(name)) for name in archive.namelist()
                            if name.startswith('chapters/') and name.endswith('.xml')]
            text = ''.join(''.join(chapter.itertext()) for chapter in chapters)
            self.assertEqual('Second chapter' in text, failure is not None or not enabled)
            self.assertTrue(all(f'Page {index} body.' in text for index in range(1, 7)))

        completed = [event for event in events if event.kind == ProgressEventKind.COMPLETED
                     and event.unit_kind == UnitKind.PAGE]
        self.assertEqual(Counter(event.page_index for event in completed), Counter(range(1, 7)))
        first = next(event for event in completed if event.page_index == 1)
        if not enabled:
            self.assertFalse(calls)
            self.assertFalse(prompts)
            self.assertEqual(first.refinement_status, RefinementStatus.DISABLED)
            return
        self.assertEqual(calls[1], round_number)
        self.assertTrue(all(count <= (2 if footnotes else 1) for count in calls.values()))
        self.assertEqual(Counter(target['page_index'] for target in prompts), Counter(range(1, 7)))
        first_target = next(target for target in prompts if target['page_index'] == 1)
        self.assertNotIn('jev_p_pass', first_target, 'incomplete review must not manufacture a score')
        self.assertEqual(first.refinement_status, RefinementStatus.FALLBACK if failure in ('request', 'validation')
                         else RefinementStatus.APPLIED)
        self.assertEqual([decision.stage for decision in decisions],
                         [] if failure is None else ['footnote_commit' if failure == 'commit' else 'footnote_repair'])
        self.assertEqual(len(observations), sum(calls.values()))
        self.assertEqual(sum(event.outcome == RequestOutcome.REQUEST_REJECTED for event in observations), 1)
        report = usage.report()
        self.assertEqual(report[options.jev].wasted.requests, 1)
        self.assertEqual(report[options.jev].effective.requests, sum(calls.values()) - 1)
        self.assertEqual(report[options.jev].effective.input_tokens, 10 * (sum(calls.values()) - 1))
        self.assertEqual(report[options.llm].wasted.requests, int(failure is not None))
        self.assertEqual(report[options.llm].effective.requests, 6 - int(failure is not None))
        self.assertEqual(report[options.llm].effective.input_tokens, 20 * (6 - int(failure is not None)))

    async def test_disabled_refinement_does_not_enable_llm(self):
        await self._extract(False, 1, None, enabled=False)

    async def test_incomplete_review_retains_normalized_and_sdk_causes(self):
        for wrapped in (False, True):
            original = input_limit()
            error = OperationError('wrapped provider rejection', cause=original) if wrapped else original
            client = client_with(error)
            options = refinement()
            raw, pages, sizes = inputs(2)
            state = next(state for index, state in build_jev_review_states(raw, pages, sizes) if index == 2)
            with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
                async with JEVRuntime(options.jev, options.resolved_jev_executor()) as runtime:
                    with self.assertRaises(IncompletePageReview) as caught:
                        await review_page(state, runtime.evaluate_questions_many, includes_footnotes=False)
            normalized = caught.exception.__cause__
            self.assertIsInstance(normalized, JEVInputLimitExceeded)
            assert isinstance(normalized, JEVInputLimitExceeded)
            self.assertIs(normalized.__cause__, error)
            self.assertEqual(client.system_one.await_count, 1)

    async def test_diagnostic_review_keeps_no_verdict_and_existing_error_policy(self):
        options = refinement()
        raw, pages, sizes = inputs(2)
        failures = []
        client = client_with(input_limit())
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(options.jev, options.resolved_jev_executor()) as runtime:
                reviewer = JevReviewProcessor(runtime.evaluate, includes_footnotes=False,
                                               question_batch_evaluator=runtime.evaluate_questions_many)
                @policy_operation
                async def run(*, error_policy=None):
                    del error_policy
                    return await reviewer(raw, pages, sizes)
                await run(error_policy=lambda failure: failures.append(failure) or True)
        self.assertFalse(reviewer.results)
        self.assertEqual(len(failures), 3)
        self.assertEqual(client.system_one.await_count, 3)

    async def test_other_provider_failures_and_normal_verdicts_keep_original_routes(self):
        errors = [
            TypeSafeBadRequestError(400, {'error_type': 'invalid_request'}, httpx2.Headers()),
            TypeSafeBadRequestError(400, {'message': 'max_tokens_exceeded'}, httpx2.Headers()),
            TypeSafeBadRequestError(400, 'max_tokens_exceeded', httpx2.Headers()),
            TypeSafeBadRequestError(400, {'error_type': 'host_limit'}, httpx2.Headers()),
            TypeSafeAPIError(503, {'error_type': 'max_tokens_exceeded'}, httpx2.Headers()),
            OperationError('network failure'),
            NonContinuableError('fatal provider failure', cause=input_limit()),
            TypeSafeAPIError(401, {'error_type': 'max_tokens_exceeded'}, httpx2.Headers()),
            TypeSafeAPIError(402, {'error_type': 'max_tokens_exceeded'}, httpx2.Headers()),
        ]
        for error in [*errors, None]:
            with self.subTest(error=error):
                await self._route_without_repair(error)

    async def _route_without_repair(self, error):
        raw, pages, sizes = inputs(2)
        options = refinement()
        def system_one(*, state, questions):
            if error is not None:
                raise error
            return SimpleNamespace(nouls={alias: SimpleNamespace(noul=.99 if name.startswith('redundancy_')
                else .01) for alias, name in zip(questions, state['checks'])})
        client = client_with(system_one)
        request = AsyncMock(side_effect=AssertionError('must not enter LLM repair'))
        decisions = []
        with TemporaryDirectory() as folder:
            from pdf_craft.common import save_xml
            from pdf_craft.pdf import encode
            root = Path(folder)
            (root/'ocr').mkdir()
            for page in raw:
                save_xml(encode(page), root/'ocr'/f'page_{page.index}.xml')
            engine = PDFExtractionEngine('unused')
            engine._ocr = cast(Any, SimpleNamespace(last_page_pixel_sizes=sizes))
            with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client), patch(
                    'pdf_craft.transform.runtime_for', return_value=SimpleNamespace(request=request)):
                @policy_operation
                async def run(*, error_policy=None, retry_policy=None):
                    del error_policy, retry_policy
                    async with engine._stream_refinement(options, {
                            'includes_footnotes': False, 'analysing_path': root}) as process:
                        return await process(*pages)
                if isinstance(error, NonContinuableError) or getattr(error, 'status', None) in (401, 402):
                    with self.assertRaises(NonContinuableError):
                        await run(error_policy=True, retry_policy=RetryPolicy(analysis=0))
                else:
                    result = await run(error_policy=lambda failure: decisions.append(failure) or True,
                                       retry_policy=RetryPolicy(analysis=0))
                    self.assertIs(result.page, pages[1])
                    self.assertEqual(result.refinement_status,
                                     RefinementStatus.NOT_REQUIRED if error is None else RefinementStatus.FALLBACK)
                    self.assertEqual(len(decisions), int(error is not None))
        self.assertEqual(client.system_one.await_count, 1)
        request.assert_not_awaited()
