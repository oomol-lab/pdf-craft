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

    async def test_map_propagates_operation_self_cancellation(self):
        executor = ConcurrentExecutor(FixedCapacity(1))

        async def cancelled(_operation_id: int):
            raise asyncio.CancelledError("inner cancellation")

        stream = executor.map([cancelled])
        with self.assertRaisesRegex(asyncio.CancelledError, "inner cancellation"):
            await anext(stream)

    async def test_map_propagates_operation_task_self_cancellation(self):
        executor = ConcurrentExecutor(FixedCapacity(1))

        async def cancelled(_operation_id: int):
            task = asyncio.current_task()
            assert task is not None
            task.cancel("task self cancellation")
            await asyncio.sleep(0)
            raise AssertionError("cancelled task continued")

        stream = executor.map([cancelled])
        with self.assertRaisesRegex(asyncio.CancelledError, "task self cancellation"):
            await asyncio.wait_for(anext(stream), timeout=1)

    async def test_map_propagates_lazy_iterator_task_self_cancellation(self):
        executor = ConcurrentExecutor(FixedCapacity(1))
        closed = False
        started = 0
        settled = 0

        async def succeeded(operation_id: int):
            nonlocal started, settled
            started += 1
            try:
                return operation_id, f"result-{operation_id}"
            finally:
                settled += 1

        class CancellingIterator:
            def __init__(self):
                self.index = 0

            def __iter__(self):
                return self

            def __next__(self):
                if self.index == 0:
                    self.index += 1
                    return succeeded
                if self.index == 1:
                    self.index += 1
                    task = asyncio.current_task()
                    assert task is not None
                    task.cancel("producer self cancellation")
                    return succeeded
                raise StopIteration

            def close(self):
                nonlocal closed
                closed = True

        stream = executor.map(CancellingIterator())
        first = await asyncio.wait_for(anext(stream), timeout=1)
        self.assertEqual((first.operation_id, first.value), (0, "result-0"))

        async def drain_until_cancelled():
            values = []
            try:
                while True:
                    values.append(await anext(stream))
            except asyncio.CancelledError as error:
                return values, error

        remaining, error = await asyncio.wait_for(
            drain_until_cancelled(), timeout=1,
        )
        self.assertEqual(str(error), "producer self cancellation")
        self.assertLessEqual(len(remaining), 1)
        self.assertTrue(closed)
        self.assertEqual(settled, started)

    async def test_map_does_not_return_partial_results_after_self_cancellation(self):
        executor = ConcurrentExecutor(FixedCapacity(2))
        release_cancelled = asyncio.Event()

        async def succeeded(operation_id: int):
            return operation_id, "complete"

        async def cancelled(_operation_id: int):
            await release_cancelled.wait()
            raise asyncio.CancelledError("missing operation")

        stream = executor.map([succeeded, cancelled])
        first = await anext(stream)
        self.assertEqual((first.operation_id, first.value), (0, "complete"))
        release_cancelled.set()
        with self.assertRaisesRegex(asyncio.CancelledError, "missing operation"):
            await anext(stream)

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

    async def test_shared_run_fatal_wakes_exhausted_map_and_settles_worker(self):
        executor = ConcurrentExecutor(FixedCapacity(2))
        worker_started = asyncio.Event()
        worker_cancelled = asyncio.Event()
        producer_exhausted = asyncio.Event()
        never = asyncio.Event()

        async def blocked(operation_id: int):
            worker_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                worker_cancelled.set()
                raise
            return operation_id, "unreachable"

        def operations():
            yield blocked
            producer_exhausted.set()

        stream = executor.map(operations())
        result_task = asyncio.ensure_future(anext(stream))
        await worker_started.wait()
        await producer_exhausted.wait()
        # Let the map consumer observe producer_done before the shared fatal.
        await asyncio.sleep(0)
        self.assertFalse(result_task.done())

        async def fatal():
            raise NonContinuableError("fatal from shared channel")

        with self.assertRaisesRegex(
            NonContinuableError, "fatal from shared channel",
        ):
            await executor.run(fatal)
        with self.assertRaisesRegex(
            NonContinuableError, "fatal from shared channel",
        ):
            await asyncio.wait_for(result_task, timeout=1)
        self.assertTrue(worker_cancelled.is_set())

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

    async def test_map_fatal_error_precedes_cancelled_sibling(self):
        for fatal_index in (0, 1):
            for _ in range(10):
                with self.subTest(fatal_index=fatal_index):
                    await self._assert_map_fatal_precedes_sibling(fatal_index)

    async def _assert_map_fatal_precedes_sibling(self, fatal_index: int):
        executor = ConcurrentExecutor(FixedCapacity(2))
        both_started = asyncio.Event()
        never = asyncio.Event()
        started = 0

        def operation(value: int):
            async def invoke(operation_id: int):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                if value == fatal_index:
                    raise NonContinuableError("quota")
                await never.wait()
                return operation_id, value
            return invoke

        with self.assertRaises(NonContinuableError):
            async for _ in executor.map([operation(0), operation(1)]):
                pass


class ExecutorCompatibilityTests(unittest.TestCase):
    def test_idle_fixed_executor_can_cross_sequential_sync_event_loops(self):
        executor = ConcurrentExecutor(FixedCapacity(1))

        async def invoke(value: int) -> int:
            return await executor.run(lambda: asyncio.sleep(0, result=value))

        self.assertEqual(asyncio.run(invoke(1)), 1)
        self.assertEqual(asyncio.run(invoke(2)), 2)


if __name__ == "__main__":
    unittest.main()
