"""Local reviews must be complete, isolated and served by the existing pool."""
# pylint: disable=unused-argument
import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from xml.etree.ElementTree import fromstring

from pdf_craft import ConcurrentExecutor, FixedCapacity, JEV, Refinement, OperationError
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.page_review import (
    JEV_QUESTION_NAME, JevReviewProcessor, build_jev_review_requests, review_threshold,
)
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.jev import JEVRuntime
from pdf_craft.pdf import decode


def page(index, count=1):
    blocks = ''.join(f'<layout ref="text" det="1,{n * 15 + 1},99,{n * 15 + 10}">'
                     f'Independent paragraph {n}.</layout>' for n in range(count))
    return decode(fromstring(f'<page index="{index}"><body>{blocks}</body></page>'))


def inputs(count=10):
    raw = [page(1), page(2, count), page(3)]
    analysed = [p for p in _project_window(raw, False) if p is not None]
    return raw, analysed, {i: (100, 300) for i in (1, 2, 3)}


def packets(count=10, footnotes=False):
    raw, analysed, sizes = inputs(count)
    return list(build_jev_review_requests(raw, analysed, sizes,
                includes_footnotes=footnotes, target_page_index=2))


class LocalReviewPacketTests(unittest.TestCase):
    def test_body_only_reviews_each_decision_in_small_independent_batches(self):
        requests = packets()
        names = [name for _, request in requests for name in request['questions']]
        self.assertEqual(set(names), {f'{family}_{n}' for family in ('gap', 'owner') for n in range(10)})
        self.assertEqual(len(names), 20)
        self.assertEqual(len(requests), 6)
        for index, request in requests:
            self.assertEqual(index, 2)
            self.assertLessEqual(len(request['questions']), 4)
            self.assertEqual(set(request['questions']), set(request['state']['checks']))
            self.assertEqual(request['state']['local_raw_evidence']['target_page']['page_index'], 2)
            self.assertNotIn(JEV_QUESTION_NAME, request['questions'])
        requests[0][1]['state']['local_raw_evidence']['target_page']['layouts'].clear()
        self.assertEqual(len(requests[1][1]['state']['local_raw_evidence']['target_page']['layouts']), 10)

    def test_decisions_live_in_checks_not_in_raw_evidence(self):
        request = packets(1)[0][1]
        raw = request['state']['local_raw_evidence']['target_page']['layouts'][0]
        self.assertNotIn('continues_from_previous', raw)
        self.assertNotIn('analysed_text', raw)
        self.assertIn('present_decision', request['state']['checks']['gap_0'])
        candidates = request['state']['checks']['gap_0']['preceding_source_candidates']
        self.assertEqual(candidates, ['p1_s0'])
        self.assertNotIn('p3_s0', candidates)

    def test_footnote_protocol_and_its_working_point_are_not_recalibrated(self):
        requests = packets(1, footnotes=True)
        self.assertEqual(len(requests), 1)
        self.assertIn('citation_standard', requests[0][1]['questions'][JEV_QUESTION_NAME]['instructions'])
        self.assertEqual(review_threshold(True), 0.45)
        self.assertEqual(review_threshold(False), 0.52)
        self.assertNotIn('risk_threshold', inspect.signature(Refinement).parameters)

    def test_empty_body_has_no_fake_provider_review(self):
        self.assertEqual(packets(0), [])


class LocalReviewRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_aggregates_requested_named_scores_and_rejects_missing_answers(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(side_effect=[
            SimpleNamespace(nouls={'gap_0': SimpleNamespace(noul=.9), 'gap_1': SimpleNamespace(noul=.2)}),
            SimpleNamespace(nouls={'gap_0': SimpleNamespace(noul=.9)}),
        ])
        request = {'state': {}, 'questions': {'gap_0': {'type': 'noul'}, 'gap_1': {'type': 'noul'}}}
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(1))) as runtime:
                self.assertEqual(await runtime.evaluate(2, request), .2)
                with self.assertRaises(OperationError) as caught:
                    await runtime.evaluate(2, request)
                self.assertIsInstance(caught.exception.__cause__, KeyError)
        self.assertEqual(client.system_one.await_count, 2)

    async def test_incomplete_batch_cannot_be_adopted_as_a_successful_page(self):
        raw, analysed, sizes = inputs(5)
        seen = []
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        async def system_one(*, state, questions):
            if 'owner_4' in questions:
                return SimpleNamespace(nouls={})
            return SimpleNamespace(nouls={name: SimpleNamespace(noul=.99) for name in questions})
        client.system_one = AsyncMock(side_effect=system_one)
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(2))) as runtime:
                reviewer = JevReviewProcessor(runtime.evaluate, batch_evaluator=runtime.evaluate_many,
                                               includes_footnotes=False)
                @policy_operation
                async def run(*, error_policy=None):
                    return await reviewer(raw, analysed, sizes)
                returned = await run(error_policy=lambda failure: seen.append(failure) or True)
        self.assertIs(returned, analysed)
        self.assertNotIn(2, [result.page_index for result in reviewer.results])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].targets[0].page_indexes, (2,))
        self.assertEqual({loc.page_indexes for loc in seen[0].context_locations}, {(1,), (3,)})

    async def test_question_batches_are_concurrent_within_the_supplied_capacity(self):
        active, peak = 0, 0
        admitted = asyncio.Event()
        release = asyncio.Event()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        async def system_one(*, state, questions):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                admitted.set()
            try:
                await release.wait()
                return SimpleNamespace(nouls={name: SimpleNamespace(noul=.99) for name in questions})
            finally:
                active -= 1
        client.system_one = AsyncMock(side_effect=system_one)
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(2))) as runtime:
                task = asyncio.create_task(runtime.evaluate_many(packets()))
                try:
                    await asyncio.wait_for(admitted.wait(), 1)
                    self.assertFalse(task.done())
                    self.assertEqual(peak, 2)
                finally:
                    release.set()
                    results = await asyncio.wait_for(task, 2)
        self.assertEqual(len(results), 6)
        self.assertEqual(peak, 2)

    async def test_local_batch_results_reduce_to_one_unrounded_page_decision(self):
        raw, analysed, sizes = inputs(5)
        async def evaluate(_index, request):
            return .48 if 'gap_4' in request['questions'] else .99
        reviewer = JevReviewProcessor(evaluate, includes_footnotes=False)
        await reviewer(raw, analysed, sizes)
        current = next(result for result in reviewer.results if result.page_index == 2)
        self.assertTrue(current.requires_review)
        self.assertEqual(current.pass_probability, .48)
        self.assertEqual(current.risk, 1 - .48)
