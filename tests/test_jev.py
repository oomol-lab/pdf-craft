import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, JEV, OperationError, RateLimitedError,
)
from pdf_craft.jev import JEVRuntime
from pdf_craft.extractor.chapter.page_review import JEV_QUESTION_NAME


class JEVTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_rejects_invalid_limits(self):
        with self.assertRaisesRegex(ValueError, "retry_times"):
            JEV("key", retry_times=-1)

    def test_configuration_has_no_independent_concurrency_channel(self):
        self.assertNotIn("concurrency", inspect.signature(JEV).parameters)

    def test_configuration_repr_hides_api_key(self):
        representation = repr(JEV("private-jev-key"))

        self.assertNotIn("private-jev-key", representation)

    async def test_runtime_uses_official_async_client_and_typed_noul(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(return_value=SimpleNamespace(
            nouls={JEV_QUESTION_NAME: SimpleNamespace(noul=0.37)}
        ))
        request = {
            "state": {"target_page": {"page_index": 7}},
            "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
        }

        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client) as create:
            async with JEVRuntime(JEV(
                "secret", model="jev-fixed", url="https://jev.invalid",
                timeout=12, retry_times=3,
            ), ConcurrentExecutor(FixedCapacity(2))) as runtime:
                probability = await runtime.evaluate(7, request)

        self.assertEqual(probability, 0.37)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["api_key"], "secret")
        self.assertEqual(create.call_args.kwargs["model"], "jev-fixed")
        self.assertEqual(create.call_args.kwargs["base_url"], "https://jev.invalid")
        self.assertEqual(create.call_args.kwargs["retry"].max_retries, 0)
        client.system_one.assert_awaited_once_with(
            state=request["state"], questions=request["questions"]
        )
        client.__aexit__.assert_awaited_once()

    async def test_rate_limit_retry_reacquires_executor_capacity(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(side_effect=[
            RateLimitedError(retry_after=0),
            SimpleNamespace(nouls={
                JEV_QUESTION_NAME: SimpleNamespace(noul=0.81),
            }),
        ])
        request = {
            "state": {"target_page": {"page_index": 3}},
            "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
        }

        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("secret", retry_times=1),
                ConcurrentExecutor(FixedCapacity(1)),
            ) as runtime:
                self.assertEqual(await runtime.evaluate(3, request), 0.81)

        self.assertEqual(client.system_one.await_count, 2)

    async def test_rate_limit_without_retry_after_uses_default_delay(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.system_one = AsyncMock(side_effect=[
            RateLimitedError(),
            SimpleNamespace(nouls={
                JEV_QUESTION_NAME: SimpleNamespace(noul=0.72),
            }),
        ])
        request = {
            "state": {"target_page": {"page_index": 4}},
            "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
        }
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client), patch(
            "pdf_craft.jev.asyncio.sleep", new_callable=AsyncMock,
        ) as sleep:
            async with JEVRuntime(
                JEV("secret", retry_times=1),
                ConcurrentExecutor(FixedCapacity(1)),
            ) as runtime:
                self.assertEqual(await runtime.evaluate(4, request), 0.72)
        sleep.assert_awaited_once_with(0.5)

    async def test_batch_retry_is_not_blocked_by_slow_initial_sibling(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        retried = asyncio.Event()
        page_one_calls = 0

        async def system_one(*, state, questions):
            nonlocal page_one_calls
            del questions
            page_index = state["target_page"]["page_index"]
            if page_index == 1:
                page_one_calls += 1
                if page_one_calls == 1:
                    raise RateLimitedError(retry_after=0)
                retried.set()
                return SimpleNamespace(nouls={
                    JEV_QUESTION_NAME: SimpleNamespace(noul=0.1),
                })
            await asyncio.wait_for(retried.wait(), 1)
            return SimpleNamespace(nouls={
                JEV_QUESTION_NAME: SimpleNamespace(noul=0.2),
            })

        client.system_one = system_one
        requests = [
            (page_index, {
                "state": {"target_page": {"page_index": page_index}},
                "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
            })
            for page_index in (1, 2)
        ]
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("secret", retry_times=1),
                ConcurrentExecutor(FixedCapacity(2)),
            ) as runtime:
                results = await asyncio.wait_for(
                    runtime.evaluate_many(requests), 1,
                )

        self.assertEqual(results, [(1, 0.1), (2, 0.2)])
        self.assertEqual(page_one_calls, 2)

    async def test_terminal_batch_error_cancels_and_settles_sibling_request(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        both_started = asyncio.Event()
        sibling_cancelled = asyncio.Event()
        never = asyncio.Event()
        started = 0

        async def system_one(*, state, questions):
            nonlocal started
            del questions
            page_index = state["target_page"]["page_index"]
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            if page_index == 1:
                raise OperationError("invalid request")
            try:
                await never.wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

        client.system_one = system_one
        requests = [
            (page_index, {
                "state": {"target_page": {"page_index": page_index}},
                "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
            })
            for page_index in (1, 2)
        ]
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("secret"), ConcurrentExecutor(FixedCapacity(2)),
            ) as runtime:
                with self.assertRaises(OperationError):
                    await runtime.evaluate_many(requests)
                self.assertTrue(sibling_cancelled.is_set())

        client.__aexit__.assert_awaited_once()

    async def test_batch_consumes_request_generator_only_when_capacity_opens(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        two_started = asyncio.Event()
        release = asyncio.Event()
        started = 0
        consumed = 0

        async def system_one(*, state, questions):
            nonlocal started
            del questions
            started += 1
            if started == 2:
                two_started.set()
            await release.wait()
            return SimpleNamespace(nouls={
                JEV_QUESTION_NAME: SimpleNamespace(
                    noul=state["target_page"]["page_index"] / 10,
                ),
            })

        def requests():
            nonlocal consumed
            for page_index in range(1, 5):
                consumed += 1
                yield page_index, {
                    "state": {"target_page": {"page_index": page_index}},
                    "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
                }

        client.system_one = system_one
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("secret"), ConcurrentExecutor(FixedCapacity(2)),
            ) as runtime:
                pending = asyncio.create_task(runtime.evaluate_many(requests()))
                await asyncio.wait_for(two_started.wait(), 1)
                self.assertEqual(consumed, 2)
                release.set()
                self.assertEqual(
                    [page for page, _ in await pending], [1, 2, 3, 4],
                )

    async def test_batch_self_cancellation_cannot_return_missing_pages(self):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        success_finished = asyncio.Event()

        async def system_one(*, state, questions):
            del questions
            page_index = state["target_page"]["page_index"]
            if page_index == 1:
                await success_finished.wait()
                raise asyncio.CancelledError("provider cancelled page")
            success_finished.set()
            return SimpleNamespace(nouls={
                JEV_QUESTION_NAME: SimpleNamespace(noul=0.8),
            })

        client.system_one = system_one
        requests = [
            (page_index, {
                "state": {"target_page": {"page_index": page_index}},
                "questions": {JEV_QUESTION_NAME: {"type": "noul"}},
            })
            for page_index in (1, 2)
        ]
        with patch("pdf_craft.jev.AsyncTypeSafeClient", return_value=client):
            async with JEVRuntime(
                JEV("secret"), ConcurrentExecutor(FixedCapacity(2)),
            ) as runtime:
                with self.assertRaisesRegex(
                    asyncio.CancelledError, "provider cancelled page",
                ):
                    await runtime.evaluate_many(requests)

        client.__aexit__.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
