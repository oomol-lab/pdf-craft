# pylint: disable=protected-access
import tempfile
import unittest
from pathlib import Path

from pdf_craft.llm import LLM, Message, MessageRole, runtime_for
from pdf_craft.llm.runtime import LLMEmptyResponseError, LLMTransportError


def _config(path: Path) -> LLM:
    return LLM("key", "https://example.invalid/v1", "model", "o200k_base",
               retry_times=1, retry_interval_seconds=0, cache_path=path / "cache",
               log_dir_path=path / "logs")


class TestLLMRuntime(unittest.TestCase):
    def test_cache_commits_only_after_context_success_and_writes_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = runtime_for(_config(root))
            calls = 0

            def invoke(*_args):
                nonlocal calls
                calls += 1
                return "ok"

            runtime._invoke = invoke  # type: ignore[method-assign]
            with runtime.context("seed") as context:
                self.assertEqual(context.request([Message(MessageRole.USER, "hello")]), "ok")
            self.assertEqual(runtime.request([Message(MessageRole.USER, "hello")], cache_seed_content="seed"), "ok")
            self.assertEqual(calls, 1)
            self.assertTrue(list((root / "logs").glob("*.log")))

    def test_empty_response_is_typed_after_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(_config(Path(directory)))
            runtime._invoke = lambda *args: ""  # type: ignore[method-assign]
            with self.assertRaises(LLMEmptyResponseError) as raised:
                runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 2)

    def test_transport_failure_reports_attempts_and_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = runtime_for(_config(Path(directory)))
            runtime._invoke = lambda *args: (_ for _ in ()).throw(ValueError("bad credentials"))  # type: ignore[method-assign]
            with self.assertRaises(LLMTransportError) as raised:
                runtime.request("hello", use_cache=False)
            self.assertEqual(raised.exception.attempts, 1)
            self.assertIsInstance(raised.exception.__cause__, ValueError)


if __name__ == "__main__":
    unittest.main()
