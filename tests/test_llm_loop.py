# pylint: disable=unused-argument
import unittest

from pdf_craft.llm import Message, MessageRole, ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop


async def _value(value):
    return value


class TestRepairLoop(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_typed_protocol_and_bounded_history(self):
        seen = []

        class Protocol:
            def validate(self, response, state, attempt, max_attempts):
                if response == "ok":
                    return ProtocolSuccess("done", state + 1)
                return ProtocolRetry("please retry", state + 1)

            def empty(self, state, attempt, max_attempts):
                return ProtocolRetry("non-empty", state)

            def exhausted(self, state, attempts, response):
                raise AssertionError("unexpected exhaustion")

        async def request(messages, attempt, maximum):
            seen.append(messages)
            return "bad" if attempt == 0 else "ok"

        result = await run_repair_loop(RepairLoopOptions(
            messages=[Message(MessageRole.USER, "start")], request=request,
            protocol=Protocol(), state=0, max_attempts=2, history_limit=1,
        ))
        self.assertEqual(result, "done")
        self.assertEqual(len(seen), 2)
        self.assertLessEqual(len(seen[1]), 3)

    async def test_preserves_initial_messages_when_history_is_cropped(self):
        seen = []

        class Protocol:
            def validate(self, response, state, attempt, max_attempts):
                return ProtocolSuccess("done", state) if attempt == 2 else ProtocolRetry("again", state, reset_history=False)

            def empty(self, state, attempt, max_attempts):
                return ProtocolRetry("again", state, reset_history=False)

            def exhausted(self, state, attempts, response):
                raise AssertionError("unexpected exhaustion")

        result = await run_repair_loop(RepairLoopOptions(
            messages=[Message(MessageRole.SYSTEM, "system"), Message(MessageRole.USER, "task")],
            request=lambda messages, attempt, maximum: _value(
                seen.append(messages) or "ok"
            ),
            protocol=Protocol(), state=None, max_attempts=3, history_limit=2,
        ))
        self.assertEqual(result, "done")
        self.assertTrue(all(message.role is MessageRole.SYSTEM for message in seen[1][:1]))
        self.assertEqual([message.message for message in seen[2][:2]], ["system", "task"])

    async def test_reset_history_discards_previous_retry_messages_but_keeps_initial(self):
        seen = []

        class Protocol:
            def validate(self, response, state, attempt, max_attempts):
                return ProtocolSuccess("done", state) if attempt == 2 else ProtocolRetry("again", state, reset_history=True)

            def empty(self, state, attempt, max_attempts):
                return ProtocolRetry("again", state, reset_history=True)

            def exhausted(self, state, attempts, response):
                raise AssertionError("unexpected exhaustion")

        await run_repair_loop(RepairLoopOptions(
            messages=[Message(MessageRole.SYSTEM, "system")],
            request=lambda messages, attempt, maximum: _value(
                seen.append(messages) or "bad"
            ),
            protocol=Protocol(), state=None, max_attempts=3, history_limit=4,
        ))
        self.assertEqual(len(seen[2]), 3)
        self.assertEqual(seen[2][0].message, "system")

    async def test_max_attempts_is_total_requests_and_transport_errors_propagate(self):
        calls = []

        class Protocol:
            def validate(self, response, state, attempt, max_attempts):
                return ProtocolRetry("retry", state)

            def empty(self, state, attempt, max_attempts):
                return ProtocolRetry("retry", state)

            def exhausted(self, state, attempts, response):
                return attempts

        result = await run_repair_loop(RepairLoopOptions(
            messages=[], request=lambda messages, attempt, maximum: _value(
                calls.append((attempt, maximum)) or "bad"
            ),
            protocol=Protocol(), state=None, max_attempts=3,
        ))
        self.assertEqual(result, 3)
        self.assertEqual(calls, [(0, 2), (1, 2), (2, 2)])

        async def broken(messages, attempt, maximum):
            raise RuntimeError("transport")

        with self.assertRaisesRegex(RuntimeError, "transport"):
            await run_repair_loop(RepairLoopOptions(messages=[], request=broken, protocol=Protocol(), state=None, max_attempts=3))

    async def test_protocol_callback_exceptions_propagate(self):
        class Protocol:
            def validate(self, response, state, attempt, max_attempts):
                raise ValueError("protocol")

            def empty(self, state, attempt, max_attempts):
                raise ValueError("protocol-empty")

            def exhausted(self, state, attempts, response):
                raise AssertionError("unexpected exhaustion")

        with self.assertRaisesRegex(ValueError, "protocol"):
            await run_repair_loop(RepairLoopOptions(
                messages=[], request=lambda *args: _value("response"),
                protocol=Protocol(), state=None,
            ))


if __name__ == "__main__":
    unittest.main()
