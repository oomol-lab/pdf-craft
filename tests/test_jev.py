import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pdf_craft import JEV
from pdf_craft.jev import JEVRuntime
from pdf_craft.extractor.chapter.page_review import JEV_QUESTION_NAME


class JEVTests(unittest.IsolatedAsyncioTestCase):
    def test_configuration_rejects_invalid_limits(self):
        with self.assertRaisesRegex(ValueError, "concurrency"):
            JEV("key", concurrency=0)
        with self.assertRaisesRegex(ValueError, "retry_times"):
            JEV("key", retry_times=-1)

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
            )) as runtime:
                probability = await runtime.evaluate(7, request)

        self.assertEqual(probability, 0.37)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["api_key"], "secret")
        self.assertEqual(create.call_args.kwargs["model"], "jev-fixed")
        self.assertEqual(create.call_args.kwargs["base_url"], "https://jev.invalid")
        client.system_one.assert_awaited_once_with(
            state=request["state"], questions=request["questions"]
        )
        client.__aexit__.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
