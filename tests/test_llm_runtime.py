
from pdf_craft import RetryPolicy
# pylint: disable=protected-access
import asyncio
import tempfile
import unittest
from inspect import signature
from os import chdir
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from xml.etree.ElementTree import Element

import httpx

from pdf_craft import ConcurrentExecutor, FixedCapacity, NonContinuableError
from pdf_craft.llm import LLM, Message, MessageRole, runtime_for
from pdf_craft.llm.runtime import (
    LLMEmptyResponseError, LLMRuntime, LLMTransportError, _provider_error,
)
from pdf_craft.pipeline.epub.translation.translator import translate as translate_epub
from pdf_craft.transformer.xml_translator import (
    SubmitKind, TranslationTask, XMLTranslator,
)


def _config(path: Path) -> LLM:
    return LLM("key", "https://example.invalid/v1", "model", "o200k_base",
               retry_times=1, retry_interval_seconds=0, cache_path=path / "cache",
               log_dir_path=path / "logs")


class TestLLMRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_request_has_an_absolute_deadline(self):
        """Regression: a stream that keeps yielding must still time out."""
        with tempfile.TemporaryDirectory() as directory:
            config = LLM(
                "key", "https://example.invalid/v1", "model", "o200k_base",
                total_timeout=0.03, retry_times=0, log_dir_path=Path(directory) / "logs",
            )
            runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))

            class SlowStream:
                def __init__(self):
                    self.closed = False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.sleep(0.01)
                    return Mock(
                        choices=[Mock(delta=Mock(content="still alive"))],
                    )

                async def aclose(self):
                    self.closed = True

            stream = SlowStream()

            class Completions:
                async def create(self, **_kwargs):
                    return stream

            class Client:
                def __init__(self, **_kwargs):
                    self.chat = Mock(completions=Completions())

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return False

            with patch("pdf_craft.llm.runtime.openai.AsyncOpenAI", Client):
                with self.assertRaises(LLMTransportError) as raised:
                    await asyncio.wait_for(
                        runtime.request("hello", use_cache=False), timeout=0.2,
                    )

            self.assertIsInstance(raised.exception.__cause__, TimeoutError)
            self.assertTrue(stream.closed)
            log_text = "\n".join(path.read_text() for path in (Path(directory) / "logs").glob("*.log"))
            self.assertIn('"category": "timeout"', log_text)

    async def test_absolute_deadline_is_shared_by_transport_retries(self):
        config = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            total_timeout=0.04, retry_times=5, retry_interval_seconds=0,
        )
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
        calls = 0

        async def invoke(*_args):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.03)
            raise httpx.ReadTimeout("slow provider")

        runtime._invoke = invoke  # type: ignore[method-assign]
        with self.assertRaises(LLMTransportError) as raised:
            await asyncio.wait_for(
                runtime.request("hello", use_cache=False), timeout=0.15,
            )

        self.assertIsInstance(raised.exception.__cause__, TimeoutError)
        self.assertLess(calls, 6)

    async def test_retry_interval_is_bounded_by_absolute_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            config = LLM(
                "key", "https://example.invalid/v1", "model", "o200k_base",
                total_timeout=0.03, retry_times=1, retry_interval_seconds=0.2,
                log_dir_path=Path(directory) / "logs",
            )
            runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
            runtime._invoke = AsyncMock(  # type: ignore[method-assign]
                side_effect=httpx.ReadTimeout("retryable provider failure"),
            )

            started = asyncio.get_running_loop().time()
            with self.assertRaises(LLMTransportError) as raised:
                await asyncio.wait_for(
                    runtime.request("hello", use_cache=False), timeout=0.12,
                )

            self.assertLess(asyncio.get_running_loop().time() - started, 0.1)
            self.assertIsInstance(raised.exception.__cause__, TimeoutError)
            log_text = "\n".join(path.read_text() for path in (Path(directory) / "logs").glob("*.log"))
            self.assertIn('"category": "timeout"', log_text)

    async def test_cancelling_streaming_request_closes_the_stream(self):
        config = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            timeout=None, retry_times=0,
        )
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
        started = asyncio.Event()

        class BlockingStream:
            def __init__(self):
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

            async def aclose(self):
                self.closed = True

        stream = BlockingStream()

        class Completions:
            async def create(self, **_kwargs):
                return stream

        class Client:
            def __init__(self, **_kwargs):
                self.chat = Mock(completions=Completions())

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with patch("pdf_craft.llm.runtime.openai.AsyncOpenAI", Client):
            task = asyncio.create_task(runtime.request("hello", use_cache=False))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(stream.closed)

    async def test_llm_events_propagate_without_a_file_log_directory(self):
        config = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            retry_times=0,
        )
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
        runtime._invoke = AsyncMock(return_value="ok")  # type: ignore[method-assign]

        with self.assertLogs("pdf_craft.llm", level="INFO") as captured:
            await runtime.request("hello", use_cache=False)

        self.assertTrue(any('"category": "request"' in line for line in captured.output))
        self.assertTrue(any('"category": "success"' in line for line in captured.output))

    async def test_runtime_requires_explicit_executor(self):
        config = LLM("key", "https://example.invalid/v1", "model", "o200k_base")
        executor = ConcurrentExecutor(FixedCapacity(1))
        runtime = LLMRuntime(config, executor=executor, protocol_version="explicit")
        runtime._invoke = AsyncMock(return_value="ok")  # type: ignore[method-assign]

        self.assertIs(runtime.executor, executor)
        self.assertEqual(runtime.protocol_version, "explicit")
        self.assertEqual(await runtime.request("hello", use_cache=False), "ok")

    async def test_xml_translator_uses_explicit_executor_and_window(self):
        config = LLM("key", "https://example.invalid/v1", "model", "o200k_base")
        executor = ConcurrentExecutor(FixedCapacity(2))
        translator = XMLTranslator(
            config, config, "en", None,   3, 10_000, "legacy-seed",
            executor=executor,
         retry_policy=RetryPolicy(fill=max(0, (1) - 1)))
        self.assertEqual(translator._cache_seed_content, "legacy-seed")
        self.assertIs(translator._translation_runtime.executor, executor)
        self.assertIs(translator._fill_runtime.executor, executor)

        class Mapper:
            def __init__(self):
                self.windows: list[int] = []

            async def map_stream_async(self, *, elements, window, **_kwargs):
                self.windows.append(window)
                for element in elements:
                    yield element, []

        mapper = Mapper()
        translator._stream_mapper = mapper  # type: ignore[assignment]
        first = TranslationTask(Element("p"), SubmitKind.REPLACE, "first")
        second = TranslationTask(Element("p"), SubmitKind.REPLACE, "second")
        third = TranslationTask(Element("p"), SubmitKind.REPLACE, "third")

        self.assertEqual(
            (await translator.translate_element(first, window=2))[1],
            "first",
        )
        self.assertEqual(
            (await translator.translate_elements((second,), window=3))[0][1],
            "second",
        )
        self.assertEqual(
            (await translator.translate_element(third, window=4))[1],
            "third",
        )
        self.assertEqual(mapper.windows, [2, 3, 4])

    def test_epub_translate_exposes_window_without_concurrency_alias(self):
        parameters = list(signature(translate_epub).parameters)
        self.assertNotIn("concurrency", parameters)
        self.assertNotIn('max_retries', parameters)
        self.assertNotIn('on_fill_failed', parameters)
        self.assertIn('retry_policy', parameters)
        self.assertIn('error_policy', parameters)

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
            runtime = runtime_for(
                _config(root), ConcurrentExecutor(FixedCapacity(1)),
            )
            calls = 0

            async def invoke(*_args):
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
            runtime = runtime_for(
                _config(deep), ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._invoke = AsyncMock(return_value="ok")  # type: ignore[method-assign]

            self.assertEqual(await runtime.request("hello", cache_seed_content="seed"), "ok")
            cached = list((deep / "cache").glob("*.txt"))
            self.assertEqual(len(cached), 1)
            self.assertLessEqual(len(cached[0].stem), 64)

    async def test_empty_response_is_typed_after_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(
                _config(Path(directory)), ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._invoke = AsyncMock(return_value="")  # type: ignore[method-assign]
            with self.assertRaises(LLMEmptyResponseError) as raised:
                await runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 2)

    async def test_transport_failure_reports_attempts_and_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(
                _config(Path(directory)), ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._invoke = AsyncMock(side_effect=ValueError("bad credentials"))  # type: ignore[method-assign]
            with self.assertRaises(LLMTransportError) as raised:
                await runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 1)
            self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_429_insufficient_quota_is_non_continuable(self):
        request = httpx.Request("POST", "https://example.invalid/v1/chat")
        response = httpx.Response(429, request=request, json={
            "error": {"type": "insufficient_quota", "code": None},
        })

        class ProviderError(Exception):
            status_code = 429

            def __init__(self):
                super().__init__("quota")
                self.response = response

        error = ProviderError()
        classified = _provider_error(error)

        self.assertIsInstance(classified, NonContinuableError)
        self.assertIs(classified.__cause__, error)

    async def test_async_transport_retries_connect_failure_over_ipv4(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(
                _config(Path(directory)), ConcurrentExecutor(FixedCapacity(1)),
            )
            invoke = AsyncMock(side_effect=[httpx.ConnectError("TLS failed"), "ok"])
            runtime._invoke_stream = invoke  # type: ignore[method-assign]

            result = await runtime.request("hello", use_cache=False)

            self.assertEqual(result, "ok")
            self.assertEqual(
                [call.kwargs["force_ipv4"] for call in invoke.await_args_list],
                [False, True],
            )

    async def test_chinese_target_preserves_chinese_dominant_text_without_llm(self):
        config = LLM("key", "https://example.invalid/v1", "model", "o200k_base")
        translator = XMLTranslator(
            config, config, "zh", None,   3, 10_000,
            executor=ConcurrentExecutor(FixedCapacity(1)),
         retry_policy=RetryPolicy(fill=max(0, (1) - 1)))
        runtime = Mock()
        translator._translation_runtime = runtime  # type: ignore[assignment]
        source = "这是已经写成中文的正文，其中保留 API 和 Lacan 等专名。"

        self.assertEqual(await translator._translate_text_async(source), source)
        runtime.context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
