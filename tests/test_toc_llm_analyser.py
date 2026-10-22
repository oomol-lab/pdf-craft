# pylint: disable=protected-access
import asyncio
import time
import unittest
from typing import Any, cast

from pdf_craft.llm.types import Message, MessageRole
from pdf_craft.llm.core import LLM
from pdf_craft.extractor.toc.llm_analyser import (
    LLMAnalysisError,
    _LLMAnalyser,
    _validate_title_response,
    _validate_toc_response,
)


class _BrokenLLM:
    async def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        raise RuntimeError("blocked by upstream")


class _JsonLLM:
    async def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        return '{"0": 0, "1": 1}'


class _SequenceLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        self.calls += 1
        return next(self.responses)


class _ResultLLM:
    async def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        return 'ANALYSIS: example {"A": 99}\nRESULT: {"A": 0, "B": 1}\nRESULT: {"A": 2, "B": 3}'


class TestLLMAnalyser(unittest.IsolatedAsyncioTestCase):
    async def test_wraps_llm_request_errors_as_analysis_error(self):
        analyser = _LLMAnalyser(
            llm=cast(LLM, _BrokenLLM()),
            validate=lambda response, payload: (response, None),  # pragma: no cover
        )

        with self.assertRaises(LLMAnalysisError) as context:
            await analyser.request(
                payload=1,
                messages=(
                    Message(role=MessageRole.SYSTEM, message="system"),
                    Message(role=MessageRole.USER, message="user"),
                ),
            )

        self.assertIn("LLM request failed at attempt 1", str(context.exception))
        self.assertIsInstance(context.exception.__cause__, RuntimeError)

    async def test_accepts_json_extracted_by_guaranteed_layer(self):
        analyser = _LLMAnalyser(
            llm=cast(LLM, _JsonLLM()),
            validate=lambda response, payload: ([0, 1], None)
            if response == '{"0": 0, "1": 1}' else (None, "unexpected response"),
        )
        self.assertEqual(
            await analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")]),
            [0, 1],
        )

    async def test_non_object_response_is_schema_retry(self):
        llm = _SequenceLLM(["[1, 2]", '{"0": 0, "1": 1}'])
        analyser = _LLMAnalyser(
            llm=cast(LLM, llm),
            validate=lambda response, payload: ([0, 1], None),
        )
        self.assertEqual(await analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")]), [0, 1])
        self.assertEqual(llm.calls, 2)

    async def test_non_object_response_exhausts_as_typed_schema_failure(self):
        llm = _SequenceLLM(["[1, 2]"] * 4)
        analyser = _LLMAnalyser(
            llm=cast(LLM, llm),
            validate=lambda response, payload: ([0, 1], None),
        )
        with self.assertRaises(LLMAnalysisError) as context:
            await analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")])
        self.assertIn("schema validation failed", str(context.exception))
        self.assertEqual(llm.calls, 4)

    async def test_toc_business_retries_share_one_transport_deadline(self):
        llm = LLM(
            "key", "https://example.invalid/v1", "model", "o200k_base",
            total_timeout=0.04, retry_times=0,
        )
        analyser = _LLMAnalyser(
            llm=llm,
            validate=lambda _response, _payload: (None, "retry"),
        )
        calls = 0

        async def slow_invalid_response(*_args):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return '{"0": 99}'

        cast(Any, analyser._runtime)._invoke = slow_invalid_response
        started = time.monotonic()
        with self.assertRaises(LLMAnalysisError) as raised:
            await asyncio.wait_for(
                analyser.request(payload=1, messages=[Message(MessageRole.USER, "toc")]),
                timeout=0.12,
            )

        self.assertLess(time.monotonic() - started, 0.09)
        self.assertLess(calls, 3)
        self.assertIn("total timeout", str(raised.exception.__cause__))

    async def test_toc_validator_receives_last_result_section(self):
        received = []
        analyser = _LLMAnalyser(
            llm=cast(LLM, _ResultLLM()),
            validate=lambda response, payload: (received.append(response) or ([2, 3], None)),
        )
        self.assertEqual(await analyser.request(payload=None, messages=[Message(MessageRole.USER, "toc")]), [2, 3])
        self.assertEqual(received, ['{"A": 2, "B": 3}'])

    def test_title_and_toc_levels_reject_string_and_boolean_integers(self):
        for response, validator in (
            ('{"0": "1"}', _validate_title_response),
            ('{"0": true}', _validate_title_response),
        ):
            result, error = validator(response, 1)
            self.assertIsNone(result)
            self.assertIn("integer", error or "")

        for response in ('{"A": "1"}', '{"A": false}'):
            result, error = _validate_toc_response(response, 1)
            self.assertIsNone(result)
            self.assertIn("integer", error or "")


if __name__ == "__main__":
    unittest.main()
