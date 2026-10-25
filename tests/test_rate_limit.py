"""Deterministic admission/cancellation tests; no network or real cooldown sleeps."""
# pylint: disable=protected-access
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest
from unittest.mock import patch

from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, NonContinuableError, OperationError,
    RateLimitedError, RateLimitScope, RateLimitTimeoutError,
)
from pdf_craft.rate_limit import _WaitBudget, retry_after_seconds


class VirtualTime:
    """Advance the event loop clock while allowing normal task scheduling."""
    def __init__(self):
        self.offset = 0.0
        self.loop = asyncio.get_running_loop()
        self.original = self.loop.time
        self.patch = patch.object(self.loop, 'time', lambda: self.original() + self.offset)
        self.jitter = patch('pdf_craft.rate_limit.random.uniform', return_value=0)

    def __enter__(self):
        self.loop.set_debug(False)
        self.patch.start()
        self.jitter.start()
        return self

    def __exit__(self, *_args):
        self.patch.stop()
        self.jitter.stop()

    async def advance(self, seconds: float = 0):
        self.offset += seconds
        for _ in range(25):
            await asyncio.sleep(0)

    async def finish(self, awaitable):
        task = asyncio.ensure_future(awaitable)
        try:
            for _ in range(600):
                await self.advance(.25)
                if task.done():
                    return task.result()
            raise AssertionError('operation failed to settle within virtual time')
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    def test_parameters_and_retry_after(self):
        for field in ('cooldown_seconds', 'max_wait_seconds'):
            for value in (0, -1, float('nan'), float('inf'), True):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    RateLimitScope(**{field: value})
        self.assertEqual(retry_after_seconds('2.5'), 2.5)
        self.assertEqual(retry_after_seconds('-1'), 0)
        for value in (None, 'bad', 'nan', 'inf'):
            self.assertIsNone(retry_after_seconds(value))
        date = datetime.now(timezone.utc) + timedelta(seconds=120)
        delay = retry_after_seconds(format_datetime(date))
        assert delay is not None
        self.assertAlmostEqual(delay, 120, delta=2)

    async def test_shared_cooling_one_probe_and_gradual_recovery(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            executors = [ConcurrentExecutor(FixedCapacity(2), rate_limit_scope=scope) for _ in range(2)]
            calls = []
            probe_gate = asyncio.Event()
            async def limited():
                raise RateLimitedError(retry_after=3)
            failing = asyncio.create_task(executors[0].run(limited))
            await clock.advance()
            failing.cancel()
            await asyncio.gather(failing, return_exceptions=True)
            async def work(index):
                calls.append((index, clock.loop.time()))
                if index == 0:
                    await probe_gate.wait()
                return index
            tasks = [asyncio.create_task(executors[i % 2].run(lambda i=i: work(i))) for i in range(10)]
            await clock.advance(2)
            self.assertEqual(calls, [])
            await clock.advance(1.1)
            self.assertEqual([i for i, _ in calls], [0])
            await clock.advance(10)
            self.assertEqual(len(calls), 1, 'only one probe across both executors')
            probe_gate.set()
            await clock.advance()
            self.assertEqual(len(calls), 1, 'probe success must not drain backlog')
            await clock.advance(1.1)
            self.assertEqual([i for i, _ in calls], [0, 1])
            results = await clock.finish(asyncio.gather(*tasks))
            self.assertEqual(results, list(range(10)))
            # First recovery starts are paced, then normal service is restored.
            self.assertGreaterEqual(calls[2][1] - calls[1][1], 1)
            self.assertEqual(scope._state, 'normal')
            self.assertIsNone(scope._cause, 'recovered scopes must not retain provider tracebacks')

    async def test_probe_relimited_extends_cooldown_and_timeout_preserves_cause(self):
        with VirtualTime() as clock:
            scope = RateLimitScope(max_wait_seconds=8)
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            errors = [RateLimitedError(retry_after=2), RateLimitedError(retry_after=20)]
            starts = []
            async def work():
                starts.append(clock.loop.time())
                raise errors[min(len(starts) - 1, 1)]
            with self.assertRaises(RateLimitTimeoutError) as caught:
                await clock.finish(executor.run(work))
            self.assertEqual(len(starts), 2)
            self.assertGreaterEqual(starts[1] - starts[0], 2)
            self.assertIs(caught.exception.__cause__, errors[1])
            self.assertEqual(scope._active, 0)

    async def test_old_success_cannot_release_new_cooldown(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            executor = ConcurrentExecutor(FixedCapacity(2), rate_limit_scope=scope)
            old_started, old_release = asyncio.Event(), asyncio.Event()
            async def old():
                old_started.set()
                await old_release.wait()
            old_task = asyncio.create_task(executor.run(old))
            await old_started.wait()
            async def limited():
                raise RateLimitedError(retry_after=5)
            limited_task = asyncio.create_task(executor.run(limited))
            await clock.advance()
            limited_task.cancel()
            await asyncio.gather(limited_task, return_exceptions=True)
            old_release.set()
            await old_task
            started = asyncio.Event()
            async def new():
                started.set()
            task = asyncio.create_task(executor.run(new))
            await clock.advance(4)
            self.assertFalse(started.is_set())
            await clock.finish(task)
            self.assertTrue(started.is_set())

    async def test_cooling_does_not_hold_capacity_and_probe_cancel_releases(self):
        with VirtualTime() as clock:
            capacity = FixedCapacity(1)
            scope = RateLimitScope()
            executor = ConcurrentExecutor(capacity, rate_limit_scope=scope)
            first = True
            probe = asyncio.Event()
            async def work():
                nonlocal first
                if first:
                    first = False
                    raise RateLimitedError(retry_after=2)
                probe.set()
                await asyncio.Event().wait()
            task = asyncio.create_task(executor.run(work))
            await clock.advance()
            self.assertEqual(capacity._available, 1)
            await clock.advance(2.1)
            self.assertTrue(probe.is_set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(capacity._available, 1)
            async def succeed():
                return 7
            self.assertEqual(await clock.finish(executor.run(succeed)), 7)

    async def test_cooling_started_while_waiting_capacity_rechecks_admission(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            a = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            b = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            release, running = asyncio.Event(), asyncio.Event()
            async def occupy():
                running.set()
                await release.wait()
            occupying = asyncio.create_task(a.run(occupy))
            await running.wait()
            starts = []
            async def queued():
                starts.append(clock.loop.time())
            queued_task = asyncio.create_task(a.run(queued))
            await clock.advance()
            async def limited():
                raise RateLimitedError(retry_after=10)
            limiting = asyncio.create_task(b.run(limited))
            await clock.advance()
            limiting.cancel()
            await asyncio.gather(limiting, return_exceptions=True)
            release.set()
            await occupying
            await clock.advance(9)
            self.assertEqual(starts, [])
            await clock.finish(queued_task)
            assert isinstance(a._capacity, FixedCapacity)
            self.assertEqual(a._capacity._available, 1)

    async def test_scope_and_fatal_pool_isolation(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            a = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            b = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            c = ConcurrentExecutor(FixedCapacity(1))
            async def limited():
                raise RateLimitedError(retry_after=30)
            cooling = asyncio.create_task(a.run(limited))
            await clock.advance()
            async def succeed():
                return 'ok'
            self.assertEqual(await c.run(succeed), 'ok')
            # Fatal in one executor must not strand its own cooling waiters or
            # permanently close a shared scope belonging to another executor.
            cooling.cancel()
            await asyncio.gather(cooling, return_exceptions=True)
            await clock.advance(31)
            async def fatal():
                raise NonContinuableError('billing')
            with self.assertRaises(NonContinuableError):
                await a.run(fatal)
            self.assertEqual(await clock.finish(b.run(succeed)), 'ok')

    async def test_non_rate_probe_failure_allows_another_probe(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            calls = 0
            async def work():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RateLimitedError(retry_after=0)
                raise OperationError('network')
            with self.assertRaises(OperationError):
                await clock.finish(executor.run(work))
            async def succeed():
                return 42
            self.assertEqual(await clock.finish(executor.run(succeed)), 42)

    async def test_shared_wait_budget_is_per_logical_request(self):
        with VirtualTime() as clock:
            scope = RateLimitScope(max_wait_seconds=3)
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            calls = 0
            async def work():
                nonlocal calls
                calls += 1
                raise RateLimitedError(retry_after=2)
            task = asyncio.create_task(executor.run(work))
            await clock.advance()
            await clock.advance(2.1)
            late = asyncio.create_task(executor.run(work))
            await clock.advance(1.1)
            with self.assertRaises(RateLimitTimeoutError):
                await task
            self.assertFalse(late.done(), 'another request owns its own budget')
            late.cancel()
            await asyncio.gather(late, return_exceptions=True)

    async def test_no_hint_exponential_backoff_and_server_delay_not_capped(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            calls = []
            async def work():
                calls.append(clock.loop.time())
                if len(calls) < 4:
                    raise RateLimitedError()
                return 9
            self.assertEqual(await clock.finish(executor.run(work)), 9)
            for index, minimum in enumerate((1, 2, 4)):
                self.assertGreaterEqual(calls[index + 1] - calls[index], minimum)
            # A provider-requested delay above the local cap remains intact.
            other = RateLimitScope()
            admission = await other._acquire(_WaitBudget())
            other._finish(admission, executor, RateLimitedError(retry_after=120), started=False)
            self.assertAlmostEqual(other._until - clock.loop.time(), 120, delta=.01)

    async def test_map_rechecks_tasks_created_before_cooling_and_handles_eof(self):
        with VirtualTime() as clock:
            executor = ConcurrentExecutor(FixedCapacity(2))
            calls = []
            first = True
            async def limited(index):
                nonlocal first
                if first:
                    first = False
                    raise RateLimitedError(retry_after=4)
                calls.append(('retry', clock.loop.time()))
                return index, 'a'
            async def following(index):
                calls.append(('following', clock.loop.time()))
                return index, 'b'
            async def consume():
                return [item async for item in executor.map([limited, following])]
            task = asyncio.create_task(consume())
            await clock.advance()
            await clock.advance(3)
            self.assertEqual(calls, [], 'created but not started tasks must obey new cooling')
            result = await clock.finish(task)
            self.assertEqual({item.value for item in result}, {'a', 'b'})

    async def test_custom_capacity_and_cancelled_waiter_return_all_permits(self):
        class CustomCapacity:
            # Deliberately no public concurrency attribute.
            def __init__(self):
                self.inner = FixedCapacity(2)
                self.acquired = 0
                self.released = 0
            async def acquire(self):
                lease = await self.inner.acquire()
                self.acquired += 1
                owner = self
                class Lease:
                    async def release(self, report):
                        owner.released += 1
                        await lease.release(report)
                return Lease()
            async def close(self, error):
                await self.inner.close(error)
        with VirtualTime() as clock:
            capacity = CustomCapacity()
            executor = ConcurrentExecutor(capacity)
            started, release = asyncio.Event(), asyncio.Event()
            async def slow():
                started.set()
                await release.wait()
            slow_task = asyncio.create_task(executor.run(slow))
            await started.wait()
            first = True
            async def work():
                nonlocal first
                if first:
                    first = False
                    raise RateLimitedError(retry_after=3)
                return 1
            task = asyncio.create_task(executor.run(work))
            await clock.advance()
            waiter = asyncio.create_task(executor.run(work))
            await clock.advance()
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            release.set()
            await slow_task
            self.assertEqual(await clock.finish(task), 1)
            self.assertEqual(capacity.acquired, capacity.released)
            self.assertEqual(capacity.inner._available, 2)

    async def test_fatal_cancels_own_cooling_waiters_without_closing_other_pool(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            a = ConcurrentExecutor(FixedCapacity(2), rate_limit_scope=scope)
            b = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            go, started = asyncio.Event(), asyncio.Event()
            root = NonContinuableError('fatal')
            async def fatal():
                started.set()
                await go.wait()
                raise root
            fatal_task = asyncio.create_task(a.run(fatal))
            await started.wait()
            async def limited():
                raise RateLimitedError(retry_after=30)
            cooling = asyncio.create_task(a.run(limited))
            await clock.advance()
            go.set()
            with self.assertRaises(NonContinuableError) as caught:
                await fatal_task
            self.assertIs(caught.exception, root)
            with self.assertRaises(NonContinuableError) as caught:
                await cooling
            self.assertIs(caught.exception, root)
            async def succeed():
                return 1
            self.assertEqual(await clock.finish(b.run(succeed)), 1)

    async def test_capacity_queue_wait_is_not_rate_wait_and_external_timeout_works(self):
        with VirtualTime() as clock:
            scope = RateLimitScope(max_wait_seconds=1)
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            gate, started = asyncio.Event(), asyncio.Event()
            async def slow():
                started.set()
                await gate.wait()
            first = asyncio.create_task(executor.run(slow))
            await started.wait()
            async def succeed():
                return 2
            second = asyncio.create_task(executor.run(succeed))
            await clock.advance(20)
            self.assertFalse(second.done())
            gate.set()
            await first
            self.assertEqual(await second, 2)
            async def limit():
                raise RateLimitedError(retry_after=30)
            async def with_timeout():
                async with asyncio.timeout(.5):
                    await executor.run(limit)
            with self.assertRaises(TimeoutError):
                await clock.finish(with_timeout())
            assert isinstance(executor._capacity, FixedCapacity)
            self.assertEqual(executor._capacity._available, 1)

    async def test_multiple_limits_never_shorten_and_local_backoff_caps_at_sixty(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            owner = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            old = [await scope._acquire(_WaitBudget()) for _ in range(10)]
            scope._finish(old[0], owner, RateLimitedError(retry_after=120), started=False)
            until = scope._until
            await clock.advance(2)
            scope._finish(old[1], owner, RateLimitedError(retry_after=1), started=False)
            self.assertEqual(scope._until, until)
            other = RateLimitScope(cooldown_seconds=40)
            token = await other._acquire(_WaitBudget())
            other._finish(token, owner, RateLimitedError(), started=False)
            self.assertAlmostEqual(other._until - clock.loop.time(), 40, delta=.01)
            await clock.advance(41)
            probe = await other._acquire(_WaitBudget())
            other._finish(probe, owner, RateLimitedError(), started=False)
            self.assertAlmostEqual(other._until - clock.loop.time(), 60, delta=.01)

    async def test_custom_capacity_failure_cannot_leak_probe_admission(self):
        class BrokenCapacity:
            async def acquire(self):
                raise RuntimeError('acquire failed')
            async def close(self, error):
                del error
        with VirtualTime() as clock:
            scope = RateLimitScope()
            broken = ConcurrentExecutor(BrokenCapacity(), rate_limit_scope=scope)
            healthy = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            async def never_called():
                self.fail('capacity failure must prevent execution')
            with self.assertRaisesRegex(RuntimeError, 'acquire failed'):
                await broken.run(never_called)
            self.assertEqual(scope._active, 0)
            async def succeed():
                return 'ok'
            self.assertEqual(await clock.finish(healthy.run(succeed)), 'ok')

    async def test_late_limit_does_not_allow_overlapping_probes_across_generations(self):
        with VirtualTime() as clock:
            scope = RateLimitScope()
            executor = ConcurrentExecutor(FixedCapacity(3), rate_limit_scope=scope)
            late_go, late_started = asyncio.Event(), asyncio.Event()
            probe_go, probe_started = asyncio.Event(), asyncio.Event()
            late_calls, probe_calls = 0, 0
            async def late_limit():
                nonlocal late_calls
                late_calls += 1
                if late_calls == 1:
                    late_started.set()
                    await late_go.wait()
                    raise RateLimitedError(retry_after=1)
                return 'late'
            async def initial_limit():
                nonlocal probe_calls
                probe_calls += 1
                if probe_calls == 1:
                    raise RateLimitedError(retry_after=1)
                probe_started.set()
                await probe_go.wait()
                return 'probe'
            late = asyncio.create_task(executor.run(late_limit))
            await late_started.wait()
            probing = asyncio.create_task(executor.run(initial_limit))
            await clock.advance()
            await clock.advance(1.1)
            self.assertTrue(probe_started.is_set())
            late_go.set()
            await clock.advance()
            new_started = asyncio.Event()
            async def following():
                new_started.set()
                return 'new'
            following_task = asyncio.create_task(executor.run(following))
            await clock.advance(5)
            self.assertFalse(new_started.is_set())
            self.assertEqual(late_calls, 1, 'old probe must finish before a new probe starts')
            probe_go.set()
            self.assertEqual(set(await clock.finish(asyncio.gather(late, probing, following_task))),
                             {'late', 'probe', 'new'})

    async def test_map_capacity_reports_only_attempt_time_not_cooling(self):
        from types import SimpleNamespace
        reports = []
        class RecordingCapacity(FixedCapacity):
            async def release(self, report):
                reports.append(report)
                await super().release(report)
        with VirtualTime() as clock, patch('pdf_craft.concurrency.time', SimpleNamespace(monotonic=clock.loop.time)):
            first = True
            async def work(index):
                nonlocal first
                if first:
                    first = False
                    raise RateLimitedError(retry_after=10)
                return index, 'ok'
            executor = ConcurrentExecutor(RecordingCapacity(1))
            async def consume():
                return [item async for item in executor.map([work])]
            results = await clock.finish(consume())
            self.assertEqual(results[0].value, 'ok')
            self.assertTrue(all(report.elapsed_seconds < 1 for report in reports))

    async def _recovery_with_occupied_capacity(self, clock, scope, capacity):
        a = ConcurrentExecutor(capacity, rate_limit_scope=scope)
        b = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
        occupied = 0
        both_started, release_old = asyncio.Event(), asyncio.Event()
        async def old():
            nonlocal occupied
            occupied += 1
            if occupied == 2:
                both_started.set()
            await release_old.wait()
        old_tasks = [asyncio.create_task(a.run(old)) for _ in range(2)]
        await both_started.wait()
        first = True
        async def limit_then_succeed():
            nonlocal first
            if first:
                first = False
                raise RateLimitedError(retry_after=1)
            return 'ok'
        self.assertEqual(await clock.finish(b.run(limit_then_succeed)), 'ok')
        async def succeed():
            return 'ok'
        self.assertEqual(await clock.finish(b.run(succeed)), 'ok')
        self.assertEqual(scope._state, 'recovery')
        self.assertEqual(scope._limit, 2)
        return a, release_old, old_tasks

    async def test_run_actual_starts_remain_paced_after_capacity_unblocks_together(self):
        with VirtualTime() as clock:
            capacity = FixedCapacity(2)
            scope = RateLimitScope()
            a, release_old, old_tasks = await self._recovery_with_occupied_capacity(
                clock, scope, capacity,
            )
            starts = []
            async def work():
                starts.append(clock.loop.time())
            await clock.advance(1.1)
            first = asyncio.create_task(a.run(work))
            await clock.advance(1.1)
            second = asyncio.create_task(a.run(work))
            await clock.advance(1.1)
            self.assertEqual(starts, [])
            release_old.set()
            await asyncio.gather(*old_tasks)
            await clock.advance()
            self.assertEqual(len(starts), 1, 'capacity release must not drain delayed admissions')
            self.assertEqual(capacity._available, 2, 'spacing waits must release capacity')
            self.assertEqual(scope._successes, 1, 'deferring an unstarted reservation is not a failed attempt')
            await clock.finish(asyncio.gather(first, second))
            self.assertGreaterEqual(starts[1] - starts[0], 1)

    async def test_map_actual_starts_remain_paced_after_delayed_capacity_grants(self):
        class DelayedCapacity(FixedCapacity):
            """Model a provider whose availability changes during recovery."""
            def __init__(self):
                super().__init__(2)
                self.armed = False
                self.ready = asyncio.Event()
                self.calls = {}
                self.blocked = 0
            async def acquire(self):
                if self.armed:
                    task = asyncio.current_task()
                    self.calls[task] = self.calls.get(task, 0) + 1
                    # A map stream first takes/releases a lazy-input lease.
                    # Delay subsequent availability independently of the scope.
                    if self.calls[task] > 1 and not self.ready.is_set():
                        self.blocked += 1
                        await self.ready.wait()
                return await super().acquire()
        with VirtualTime() as clock:
            capacity = DelayedCapacity()
            a, release_old, old_tasks = await self._recovery_with_occupied_capacity(
                clock, RateLimitScope(), capacity,
            )
            release_old.set()
            await asyncio.gather(*old_tasks)
            capacity.armed = True
            starts = []
            async def work(index):
                starts.append(clock.loop.time())
                return index, 'ok'
            async def consume():
                return [item async for item in a.map([work])]
            await clock.advance(1.1)
            first = asyncio.create_task(consume())
            await clock.advance(1.1)
            second = asyncio.create_task(consume())
            await clock.advance(1.1)
            self.assertEqual(capacity.blocked, 2)
            self.assertEqual(starts, [])
            capacity.ready.set()
            await clock.advance()
            self.assertEqual(len(starts), 1, 'separate map streams must pace actual starts')
            self.assertEqual(capacity._available, 2)
            results = await clock.finish(asyncio.gather(first, second))
            self.assertEqual([items[0].value for items in results], ['ok', 'ok'])
            self.assertGreaterEqual(starts[1] - starts[0], 1)

    async def test_post_capacity_spacing_wait_honors_budget_and_cancellation(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                await self._post_capacity_spacing_wait(cancel)

    async def _post_capacity_spacing_wait(self, cancel):
        with VirtualTime() as clock:
            capacity = FixedCapacity(2)
            scope = RateLimitScope(max_wait_seconds=1.5)
            a, release_old, old_tasks = await self._recovery_with_occupied_capacity(clock, scope, capacity)
            starts = []
            async def work():
                starts.append(clock.loop.time())
            # Both requests first spend about one second waiting for the
            # recovery interval, then wait on occupied capacity (unbudgeted).
            first = asyncio.create_task(a.run(work))
            second = asyncio.create_task(a.run(work))
            await clock.advance()
            await clock.advance(1.1)
            self.assertEqual(starts, [])
            release_old.set()
            await asyncio.gather(*old_tasks)
            await clock.advance()
            self.assertEqual(len(starts), 1)
            await first
            if cancel:
                second.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await second
            else:
                with self.assertRaises(RateLimitTimeoutError):
                    await clock.finish(second)
            self.assertEqual(len(starts), 1)
            self.assertEqual(capacity._available, 2)
            self.assertEqual(scope._active, 0)
            self.assertFalse(scope._queue)
            # Neither timeout nor cancellation may strand admission or
            # erase the one actual recovery success.
            self.assertEqual(scope._successes, 1)
            await clock.finish(a.run(work))
            self.assertEqual(len(starts), 2)
