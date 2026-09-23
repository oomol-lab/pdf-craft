# pylint: disable=protected-access
import tempfile
import unittest
from os import chdir
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx

from pdf_craft.llm import LLM, Message, MessageRole, runtime_for
from pdf_craft.llm.runtime import LLMEmptyResponseError, LLMTransportError
from pdf_craft.transformer.xml_translator import XMLTranslator


def _config(path: Path) -> LLM:
    return LLM("key", "https://example.invalid/v1", "model", "o200k_base",
               retry_times=1, retry_interval_seconds=0, cache_path=path / "cache",
               log_dir_path=path / "logs")


class TestLLMRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_relative_output_paths_are_bound_at_construction(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            try:
                chdir(first)
                config = LLM(
                    "key", "https://example.invalid/v1", "model", "o200k_base",
                    cache_path="cache", log_dir_path="logs",
                )
                chdir(second)
                self.assertEqual(config.cache_path, first.resolve() / "cache")
                self.assertEqual(config.log_dir_path, first.resolve() / "logs")
            finally:
                chdir(original)

    async def test_cache_commits_only_after_context_success_and_writes_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = runtime_for(_config(root))
            calls = 0

            def invoke(*_args):
                nonlocal calls
                calls += 1
                return "ok"

            runtime._invoke = invoke  # type: ignore[method-assign]
            async with runtime.context("seed") as context:
                self.assertEqual(await context.request([Message(MessageRole.USER, "hello")]), "ok")
            self.assertEqual(await runtime.request(
                [Message(MessageRole.USER, "hello")], cache_seed_content="seed",
            ), "ok")
            self.assertEqual(calls, 1)
            self.assertTrue(list((root / "logs").glob("*.log")))

    async def test_cache_key_is_short_enough_for_deep_windows_work_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deep = root / ("nested-" + "x" * 40) / ("work-" + "y" * 40)
            runtime = runtime_for(_config(deep))
            runtime._invoke = lambda *_args: "ok"  # type: ignore[method-assign]

            self.assertEqual(await runtime.request("hello", cache_seed_content="seed"), "ok")
            cached = list((deep / "cache").glob("*.txt"))
            self.assertEqual(len(cached), 1)
            self.assertLessEqual(len(cached[0].stem), 64)

    async def test_empty_response_is_typed_after_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(_config(Path(directory)))
            runtime._invoke = lambda *args: ""  # type: ignore[method-assign]
            with self.assertRaises(LLMEmptyResponseError) as raised:
                await runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 2)

    async def test_transport_failure_reports_attempts_and_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(_config(Path(directory)))
            runtime._invoke = lambda *args: (_ for _ in ()).throw(ValueError("bad credentials"))  # type: ignore[method-assign]
            with self.assertRaises(LLMTransportError) as raised:
                await runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 1)
            self.assertIsInstance(raised.exception.__cause__, ValueError)

    async def test_async_transport_retries_connect_failure_over_ipv4(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(_config(Path(directory)))
            invoke = AsyncMock(side_effect=[httpx.ConnectError("TLS failed"), "ok"])
            runtime._invoke_stream = invoke  # type: ignore[method-assign]

            result = await runtime._invoke_async(
                [Message(MessageRole.USER, "hello")], None, None, None,
            )

            self.assertEqual(result, "ok")
            self.assertEqual(
                [call.kwargs["force_ipv4"] for call in invoke.await_args_list],
                [False, True],
            )

    async def test_chinese_target_preserves_chinese_dominant_text_without_llm(self):
        config = LLM("key", "https://example.invalid/v1", "model", "o200k_base")
        translator = XMLTranslator(config, config, "zh", None, False, 1, 3, 10_000)
        runtime = Mock()
        translator._translation_runtime = runtime  # type: ignore[assignment]
        source = "这是已经写成中文的正文，其中保留 API 和 Lacan 等专名。"

        self.assertEqual(translator._translate_text(source), source)
        self.assertEqual(await translator._translate_text_async(source), source)
        runtime.context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
