import unittest
from typing import cast

from pdf_craft.llm.types import Message, MessageRole
from pdf_craft.llm.core import LLM
from pdf_craft.toc.llm_analyser import LLMAnalysisError, _LLMAnalyser


class _BrokenLLM:
    def request(self, input):  # pylint: disable=redefined-builtin,unused-argument
        raise RuntimeError("blocked by upstream")


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


if __name__ == "__main__":
    unittest.main()
