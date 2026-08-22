import unittest

from pdf_craft.llm import Message, MessageRole, ProtocolRetry, ProtocolSuccess, RepairLoopOptions, run_repair_loop


class TestRepairLoop(unittest.TestCase):
    def test_retries_with_typed_protocol_and_bounded_history(self):
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

        def request(messages, attempt, maximum):
            seen.append(messages)
            return "bad" if attempt == 0 else "ok"

        result = run_repair_loop(RepairLoopOptions(
            messages=[Message(MessageRole.USER, "start")], request=request,
            protocol=Protocol(), state=0, max_attempts=2, history_limit=1,
        ))
        self.assertEqual(result, "done")
        self.assertEqual(len(seen), 2)
        self.assertLessEqual(len(seen[1]), 3)


if __name__ == "__main__":
    unittest.main()
