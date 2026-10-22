# pylint: disable=protected-access,unused-argument
"""Business repair budgets are independent of transport retries."""

import unittest
from types import SimpleNamespace
from typing import Any, cast
from xml.etree.ElementTree import Element

from pdf_craft import RetryPolicy
from pdf_craft._policy import OMITTED, policy_operation
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import Callbacks
from pdf_craft.transformer.xml_translator.xml_translator.translator import (
    XMLTranslator,
    XMLFillError,
)


class _Context:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, *_args, **_kwargs):
        self.calls += 1
        return next(self.responses)


class _Hill:
    def __init__(self, errors):
        self.errors = iter(errors)
        self.submissions = 0

    def request_element(self):
        return Element("xml")

    def validate(self, _element):
        return next(self.errors)

    def submit(self, _element):
        self.submissions += 1
        return None


def _translator(responses):
    translator = object.__new__(XMLTranslator)
    context = _Context(responses)

    async def template(_name):
        return SimpleNamespace(render=lambda: "fill")

    translator._fill_runtime = cast(
        Any, SimpleNamespace(context=lambda **_: context, template=template)
    )
    translator._cache_seed_content = None
    return translator, context


@policy_operation
async def _run(translator, hill, *, retry_policy=None, error_policy=OMITTED):
    await translator._request_and_submit_async(
        hill, "source", "translation", Callbacks(lambda x: x, lambda x: x, lambda x: x)
    )


class XMLRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_complete_valid_candidates_are_submitted(self):
        translator, context = _translator(["<xml>bad</xml>", "<xml>good</xml>"])
        hill = _Hill(["structure wrong", None])
        await _run(translator, hill, retry_policy=RetryPolicy(fill=1))
        self.assertEqual(context.calls, 2)
        self.assertEqual(hill.submissions, 1)

    async def test_zero_retries_means_one_attempt_and_exhaustion_raises(self):
        translator, context = _translator(["<xml>bad</xml>"])
        hill = _Hill(["final structural diagnostic"])
        with self.assertRaisesRegex(
            XMLFillError, "final structural diagnostic"
        ) as caught:
            await _run(translator, hill, retry_policy=RetryPolicy(fill=0))
        self.assertEqual(caught.exception.attempts, 1)
        self.assertEqual(context.calls, 1)
        self.assertEqual(hill.submissions, 0)

    async def test_empty_and_malformed_responses_have_bounded_attempts(self):
        translator, context = _translator(["", "not xml", "<xml/>"])
        with self.assertRaises(XMLFillError):
            await _run(translator, _Hill([]), retry_policy=RetryPolicy(fill=1))
        self.assertEqual(context.calls, 2)

    def test_extracts_no_and_multiple_blocks(self):
        translator = object.__new__(XMLTranslator)
        self.assertIn("No complete", translator._extract_xml_element("plain text"))
        self.assertIn(
            "Found 2", translator._extract_xml_element("<xml>a</xml><xml>b</xml>")
        )
