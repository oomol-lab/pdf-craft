"""Smoke observability must follow protocol revisions without rejecting real fill."""
# pylint: disable=protected-access
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from xml.etree.ElementTree import tostring

from pdf_craft import LLM, ConcurrentExecutor, FixedCapacity
from pdf_craft.llm import Message, MessageRole
from pdf_craft.llm.runtime import LLMContext
from pdf_craft.extractor.chapter.chapter import encode
from pdf_craft_tool.smoke.fill_formula_loop import _Recorder, _case
from tests.test_canonical_math_projection import chapter, expression


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

    async def test_real_smoke_case_accepts_engineered_numeric_conversion(self):
        source = chapter(['Costs exceed ', expression('4'), ' billion.'])
        responses = [
            '费用超过40亿。',
            '<xml><fragment id="1">费用超过<expression><source-math key="s1">40</source-math></expression>亿。</fragment></xml>',
        ]
        case = dict(name='numeric-conversion', source_xml=tostring(encode(source), encoding='unicode'))
        with TemporaryDirectory() as directory, patch.object(LLMContext, 'request', AsyncMock(side_effect=responses)):
            output = Path(directory)/'case'
            result = await _case(case, output, LLM('offline', 'https://example.invalid', 'offline', 'cl100k_base'),
                                 ConcurrentExecutor(FixedCapacity(1)))
            self.assertTrue(result['passed'], result)
            self.assertGreater(result['fill_requests'], 0)
            self.assertIn('>40</inline_expr>亿', (output/'accepted.xml').read_text())
