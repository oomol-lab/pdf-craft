"""Smoke observability must follow protocol revisions without rejecting real fill."""
# pylint: disable=protected-access
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from pdf_craft.llm import Message, MessageRole
from pdf_craft_tool.smoke.fill_formula_loop import _Recorder


class FillSmokeAccountingTests(IsolatedAsyncioTestCase):
    async def test_request_observation_counts_current_and_future_fill_revisions(self):
        with TemporaryDirectory() as directory:
            recorder = _Recorder(Path(directory))
            recorder._request = AsyncMock(return_value='response')
            for phase, instruction in (
                    ('free-translation-v2', 'You are a translator.'),
                    ('xml-fill-friendly-v3', 'You are an XML structure validator.'),
                    ('xml-fill-friendly-v4', 'You are an XML structure validator.'),
            ):
                context = SimpleNamespace(runtime=SimpleNamespace(protocol_version=phase))
                self.assertEqual(await recorder.request(context, [Message(MessageRole.SYSTEM, instruction)]), 'response')
            self.assertEqual(recorder.summary(), dict(translation_requests=1, fill_requests=2, groups=0))
            records = [json.loads(path.read_text()) for path in sorted(Path(directory).glob('request-*.json'))]
            self.assertEqual(len(records), 3)
            self.assertTrue(all(row['response'] == 'response' for row in records))
