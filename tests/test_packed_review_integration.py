"""Default review routing retains provider accounting and physical page limits."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import ConcurrentExecutor, FixedCapacity, JEV
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.page_review import JevReviewProcessor
from pdf_craft.jev import JEVRuntime
from pdf_craft.usage import UsageCollector
from tests.test_local_jev_review import inputs


class PackedReviewIntegrationTests(IsolatedAsyncioTestCase):
    async def test_default_processor_uses_bounded_requests_and_existing_observers(self):
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                await self._bounded_requests_and_observers(footnotes)

    async def _bounded_requests_and_observers(self, footnotes):
        raw, analyses, sizes = inputs(12)
        before = deepcopy(analyses)
        events = []
        config = JEV('unused', on_request=events.append)
        usage = UsageCollector()
        calls = Counter()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        async def system_one(*, state, questions, **kwargs):
            index = state['local_raw_evidence']['target_page']['page_index']
            calls[index] += 1
            self.assertEqual(kwargs['extra_headers'], {'X-Test': 'review'})
            return SimpleNamespace(
                nouls={name: SimpleNamespace(noul=.5) for name in questions},
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
            )

        client.system_one = AsyncMock(side_effect=system_one)
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(config, ConcurrentExecutor(FixedCapacity(2))) as runtime:
                reviewer = JevReviewProcessor(
                    runtime.evaluate, includes_footnotes=footnotes,
                    question_batch_evaluator=runtime.evaluate_questions_many)

                @policy_operation
                async def run(*, request_headers=None, usage=None):
                    del request_headers, usage  # Consumed by policy_operation.
                    return await reviewer(raw, analyses, sizes)

                result = await run(request_headers={'X-Test': 'review'}, usage=usage)

        self.assertIs(result, analyses)
        self.assertEqual(analyses, before)
        self.assertEqual(calls[2], 2 if footnotes else 1)
        self.assertTrue(all(count <= (2 if footnotes else 1) for count in calls.values()))
        self.assertEqual(len(events), sum(calls.values()))
        report = usage.report()[config]
        self.assertEqual(report.effective.requests, len(events))
        self.assertEqual(report.effective.input_tokens, 10 * len(events))
        self.assertEqual(report.effective.output_tokens, 2 * len(events))
        self.assertEqual(report.wasted.requests, 0)

    async def test_ignored_provider_failure_does_not_become_a_page_verdict(self):
        raw, analyses, sizes = inputs(12)
        failures = []
        calls = Counter()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        async def system_one(*, state, questions):
            index = state['local_raw_evidence']['target_page']['page_index']
            calls[index] += 1
            return SimpleNamespace(nouls={} if index == 2 else {
                name: SimpleNamespace(noul=.5) for name in questions})

        client.system_one = AsyncMock(side_effect=system_one)
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
            async with JEVRuntime(JEV('unused'), ConcurrentExecutor(FixedCapacity(2))) as runtime:
                reviewer = JevReviewProcessor(
                    runtime.evaluate, includes_footnotes=False,
                    question_batch_evaluator=runtime.evaluate_questions_many)

                @policy_operation
                async def run(*, error_policy=None):
                    del error_policy  # Consumed by policy_operation.
                    return await reviewer(raw, analyses, sizes)

                await run(error_policy=lambda failure: failures.append(failure) or True)

        self.assertEqual(calls[2], 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].targets[0].page_indexes, (2,))
        self.assertNotIn(2, [result.page_index for result in reviewer.results])
