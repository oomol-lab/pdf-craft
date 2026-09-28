import asyncio
import unittest

from pdf_craft import (
    ConcurrentExecutor,
    FixedCapacity,
    NonContinuableError,
    OperationError,
    RateLimitedError,
)


class AsyncExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_and_map_share_capacity_and_map_is_completion_ordered(self):
        executor = ConcurrentExecutor(FixedCapacity(2))
        active = 0
        maximum = 0
        release = asyncio.Event()

        async def enter(value: int):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if value in (0, 9):
                await release.wait()
            else:
                await asyncio.sleep(0)
            active -= 1
            return value

        run_task = asyncio.create_task(executor.run(lambda: enter(9)))

        def operations():
            for value in (1, 2, 0):
                async def operation(operation_id: int, value=value):
                    return operation_id, await enter(value)
                yield operation

        async def collect():
            return [item async for item in executor.map(operations())]

        map_task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)
        self.assertEqual(maximum, 2)
        release.set()
        results = await map_task
        self.assertEqual(await run_task, 9)
        self.assertEqual([item.value for item in results], [1, 2, 0])
        self.assertLessEqual(maximum, 2)

    async def test_map_pulls_lazily_and_closes_generator_on_early_exit(self):
        executor = ConcurrentExecutor(FixedCapacity(1))
        pulled = 0
        closed = False

        def operations():
            nonlocal pulled, closed
            try:
                for value in range(20):
                    pulled += 1

                    async def operation(operation_id: int, value=value):
                        return operation_id, value
                    yield operation
            finally:
                closed = True

        stream = executor.map(operations())
        first = await anext(stream)
        self.assertEqual(first.value, 0)
        await getattr(stream, "aclose")()
        self.assertTrue(closed)
        self.assertLess(pulled, 20)

    async def test_map_stops_pulling_while_consumer_leaves_result_queued(self):
        executor = ConcurrentExecutor(FixedCapacity(1))
        pulled = 0

        def operations():
            nonlocal pulled
            for value in range(10_000):
                pulled += 1

                async def operation(operation_id: int, value=value):
                    return operation_id, value
                yield operation

        stream = executor.map(operations())
        first = await anext(stream)
        self.assertEqual(first.value, 0)
        await asyncio.sleep(0.05)
        paused_at = pulled
        await asyncio.sleep(0.05)
        self.assertEqual(pulled, paused_at)
        self.assertLessEqual(pulled, 3)
        await getattr(stream, "aclose")()

    async def test_map_returns_recoverable_failures_with_operation_id(self):
        executor = ConcurrentExecutor(FixedCapacity(2))

        async def limited(operation_id: int):
            raise RateLimitedError(retry_after=0.5)

        async def failed(operation_id: int):
            raise ValueError("bad")

        results = [item async for item in executor.map([limited, failed])]
        by_id = {item.operation_id: item for item in results}
        limited_error = by_id[0].error
        failed_error = by_id[1].error
        self.assertIsInstance(limited_error, RateLimitedError)
        assert limited_error is not None
        self.assertEqual(limited_error.operation_id, 0)
        self.assertIsInstance(failed_error, OperationError)
        assert failed_error is not None
        self.assertIsInstance(failed_error.__cause__, ValueError)

    async def test_non_continuable_error_closes_executor_and_wakes_waiters(self):
        executor = ConcurrentExecutor(FixedCapacity(1))
        started = asyncio.Event()

        async def fatal():
            started.set()
            raise NonContinuableError("empty balance")

        first = asyncio.create_task(executor.run(fatal))
        await started.wait()
        second = asyncio.create_task(executor.run(lambda: asyncio.sleep(1)))
        with self.assertRaises(NonContinuableError):
            await first
        with self.assertRaises((NonContinuableError, asyncio.CancelledError)):
            await second
        with self.assertRaises(NonContinuableError):
            await executor.run(lambda: asyncio.sleep(0))

    async def test_map_fatal_error_discards_the_lazy_tail(self):
        executor = ConcurrentExecutor(FixedCapacity(1))
        pulled = 0
        closed = False

        def operations():
            nonlocal pulled, closed
            try:
                for value in range(10):
                    pulled += 1

                    async def operation(operation_id: int, value=value):
                        if value == 0:
                            raise NonContinuableError("quota exhausted")
                        return operation_id, value

                    yield operation
            finally:
                closed = True

        with self.assertRaises(NonContinuableError) as raised:
            async for _ in executor.map(operations()):
                pass
        self.assertEqual(raised.exception.operation_id, 0)
        self.assertEqual(pulled, 1)
        self.assertTrue(closed)


class ExecutorCompatibilityTests(unittest.TestCase):
    def test_idle_fixed_executor_can_cross_sequential_sync_event_loops(self):
        executor = ConcurrentExecutor(FixedCapacity(1))

        async def invoke(value: int) -> int:
            return await executor.run(lambda: asyncio.sleep(0, result=value))

        self.assertEqual(asyncio.run(invoke(1)), 1)
        self.assertEqual(asyncio.run(invoke(2)), 2)


if __name__ == "__main__":
    unittest.main()
