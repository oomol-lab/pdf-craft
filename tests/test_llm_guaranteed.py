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
            max_retries=2,
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
                max_retries=1,
            ))


if __name__ == "__main__":
    unittest.main()
