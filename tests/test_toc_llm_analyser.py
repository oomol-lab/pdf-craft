import unittest
from typing import cast

from pdf_craft.llm.types import Message, MessageRole
from pdf_craft.llm.core import LLM
from pdf_craft.extractor.toc.llm_analyser import LLMAnalysisError, _LLMAnalyser


class _BrokenLLM:
    def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        raise RuntimeError("blocked by upstream")


class _JsonLLM:
    def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        return '{"0": 0, "1": 1}'


class _SequenceLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        self.calls += 1
        return next(self.responses)


class _ResultLLM:
    def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        return 'ANALYSIS: example {"A": 99}\nRESULT: {"A": 0, "B": 1}\nRESULT: {"A": 2, "B": 3}'


class TestLLMAnalyser(unittest.TestCase):
    def test_wraps_llm_request_errors_as_analysis_error(self):
        analyser = _LLMAnalyser(
            llm=cast(LLM, _BrokenLLM()),
            validate=lambda response, payload: (response, None),  # pragma: no cover
        )

        with self.assertRaises(LLMAnalysisError) as context:
            analyser.request(
                payload=1,
                messages=(
                    Message(role=MessageRole.SYSTEM, message="system"),
                    Message(role=MessageRole.USER, message="user"),
                ),
            )

        self.assertIn("LLM request failed at attempt 1", str(context.exception))
        self.assertIsInstance(context.exception.__cause__, RuntimeError)

    def test_accepts_json_extracted_by_guaranteed_layer(self):
        analyser = _LLMAnalyser(
            llm=cast(LLM, _JsonLLM()),
            validate=lambda response, payload: ([0, 1], None)
            if response == '{"0": 0, "1": 1}' else (None, "unexpected response"),
        )
        self.assertEqual(
            analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")]),
            [0, 1],
        )

    def test_non_object_response_is_schema_retry(self):
        llm = _SequenceLLM(["[1, 2]", '{"0": 0, "1": 1}'])
        analyser = _LLMAnalyser(
            llm=cast(LLM, llm),
            validate=lambda response, payload: ([0, 1], None),
        )
        self.assertEqual(analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")]), [0, 1])
        self.assertEqual(llm.calls, 2)

    def test_non_object_response_exhausts_as_typed_schema_failure(self):
        llm = _SequenceLLM(["[1, 2]"] * 3)
        analyser = _LLMAnalyser(
            llm=cast(LLM, llm),
            validate=lambda response, payload: ([0, 1], None),
        )
        with self.assertRaises(LLMAnalysisError) as context:
            analyser.request(payload=None, messages=[Message(MessageRole.USER, "json")])
        self.assertIn("schema validation failed", str(context.exception))
        self.assertEqual(llm.calls, 3)

    def test_toc_validator_receives_last_result_section(self):
        received = []
        analyser = _LLMAnalyser(
            llm=cast(LLM, _ResultLLM()),
            validate=lambda response, payload: (received.append(response) or ([2, 3], None)),
        )
        self.assertEqual(analyser.request(payload=None, messages=[Message(MessageRole.USER, "toc")]), [2, 3])
        self.assertEqual(received, ['{"A": 2, "B": 3}'])


if __name__ == "__main__":
    unittest.main()
