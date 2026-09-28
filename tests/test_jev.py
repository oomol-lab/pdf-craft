import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, JEV, RateLimitedError,
)
from pdf_craft.jev import JEVRuntime
from pdf_craft.extractor.chapter.page_review import JEV_QUESTION_NAME


class JEVTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_rejects_invalid_limits(self):
        with self.assertRaisesRegex(ValueError, "retry_times"):
            JEV("key", retry_times=-1)
        with self.assertRaisesRegex(ValueError, "concurrency"):
            JEV("key", concurrency=0)

    def test_configuration_keeps_concurrency_compatibility_field(self):
        self.assertEqual(JEV("key").concurrency, 4)
        self.assertEqual(JEV("key", concurrency=7).concurrency, 7)

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


if __name__ == "__main__":
    unittest.main()
