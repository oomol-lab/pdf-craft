 # pylint: disable=protected-access,unused-argument
import unittest
from types import SimpleNamespace
from typing import Any, cast
from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.xml_translator.callbacks import Callbacks
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLTranslator


class _Context:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def request(self, messages, **kwargs):
        self.calls += 1
        return next(self.responses)


class _Runtime:
    def __init__(self, responses):
        self.context_value = _Context(responses)

    def context(self, **kwargs):
        return self.context_value


class _Hill:
    def __init__(self, errors):
        self.errors = iter(errors)

    def request_element(self):
        return Element("xml")

    def submit(self, element):
        return next(self.errors)


def _translator(responses, retries=2):
    translator = object.__new__(XMLTranslator)
    translator._fill_runtime = cast(Any, _Runtime(responses))
    translator._fill_llm = cast(Any, SimpleNamespace(template=lambda name: SimpleNamespace(render=lambda: "fill")))
    translator._cache_seed_content = None
    translator._max_retries = retries
    return translator


def _callbacks(events):
    return Callbacks(lambda x: x, lambda x: x, lambda x: x, events.append)


class TestXMLRepairLoop(unittest.TestCase):
    def test_extracts_no_and_multiple_blocks(self):
        translator = object.__new__(XMLTranslator)
        self.assertIn("No complete", translator._extract_xml_element("plain text"))
        self.assertIn("Found 2", translator._extract_xml_element("<xml>a</xml><xml>b</xml>"))

    def test_retries_hill_climbing_improvement_then_succeeds(self):
        translator = _translator(["<xml>bad</xml>", "<xml>good</xml>"])
        events = []
        hill = _Hill(["structural error", None])
        translator._request_and_submit(cast(Any, hill), "source", "translated", _callbacks(events))
        self.assertEqual(cast(Any, translator._fill_runtime).context_value.calls, 2)
        self.assertEqual([event.error_message for event in events], ["structural error"])
        self.assertFalse(events[0].over_maximum_retries)

    def test_exhausted_event_keeps_final_xml_diagnostic_and_callback_errors_propagate(self):
        translator = _translator(["<xml>a</xml>", "<xml>b</xml>"], retries=2)
        events = []
        translator._request_and_submit(cast(Any, _Hill(["first error", "final structural error"])), "s", "t", _callbacks(events))
        self.assertEqual(events[-1].error_message, "final structural error")
        self.assertTrue(events[-1].over_maximum_retries)

        def fail(event):
            raise RuntimeError("callback")

        with self.assertRaisesRegex(RuntimeError, "callback"):
            _translator(["<xml>x</xml>"])._request_and_submit(
                cast(Any, _Hill(["error"])), "s", "t",
                Callbacks(lambda x: x, lambda x: x, lambda x: x, fail),
            )


if __name__ == "__main__":
    unittest.main()
