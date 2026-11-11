
from pdf_craft import RetryPolicy
import unittest
from typing import cast

from pydantic import RootModel

from pdf_craft.llm import Message, MessageRole
from pdf_craft.llm.guaranteed import GuaranteedOptions, request_guaranteed_json


class _Payload(RootModel[dict[str, int]]):
    pass


class TestGuaranteedJson(unittest.IsolatedAsyncioTestCase):
    async def test_repairs_json_and_retries_schema_feedback(self):
        calls = []

        async def request(messages, index, _maximum):
            calls.append(messages)
            return '{"value":}' if index == 0 else '{"value": 3}'

        result = await request_guaranteed_json(GuaranteedOptions(
            messages=[Message(MessageRole.USER, "return json")],
            request=request,
            schema=_Payload,
            parse=lambda data, index, maximum: cast(_Payload, data).root["value"],
            retry_policy=RetryPolicy(analysis=2),
        ))
        self.assertEqual(result, 3)
        self.assertEqual(len(calls), 2)
        self.assertIn("assistant", calls[1][1].role.name.lower())

    async def test_empty_response_exhausts(self):
        async def request(_messages, _index, _maximum):
            return ""

        with self.assertRaises(Exception):
            await request_guaranteed_json(GuaranteedOptions(
                messages=[], request=request,
                schema=_Payload, parse=lambda data, index, maximum: data,
                retry_policy=RetryPolicy(analysis=1),
            ))

    async def test_complete_rebuild_uses_initial_evidence_and_feedback_without_rejected_json(self):
        calls = []
        responses = iter(('  ', 'not JSON', '{"value":"wrong-type"}', '{"value":-1}', '{"value":3}'))

        async def request(messages, _index, _maximum):
            calls.append(messages)
            return next(responses)

        def parse(data, _index, _maximum):
            value = data.root['value']
            if value < 0:
                raise ValueError('Negative fixture value')
            return value

        initial = [Message(MessageRole.USER, 'immutable evidence')]
        result = await request_guaranteed_json(GuaranteedOptions(
            messages=initial, request=request, schema=_Payload, parse=parse,
            retry_policy=RetryPolicy(analysis=4), include_response=False,
        ))
        self.assertEqual(result, 3)
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0], initial)
        for messages in calls[1:]:
            self.assertEqual(messages[0], initial[0])
            self.assertEqual(len(messages), 2)
            self.assertTrue(all(item.role is MessageRole.USER for item in messages))
        self.assertIn('Negative fixture value', calls[-1][-1].message)


if __name__ == "__main__":
    unittest.main()
