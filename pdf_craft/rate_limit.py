"""Shared, provider-independent admission after temporary rate limiting."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from contextvars import ContextVar
from functools import wraps
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
from weakref import WeakKeyDictionary
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from ._operation import OperationError, RateLimitedError

_P = ParamSpec("_P")
_T = TypeVar("_T")


class RateLimitTimeoutError(OperationError):
    """A logical request exhausted its cumulative rate-limit waiting budget."""


@dataclass
class _WaitBudget:
    waited: float = 0.0
    cause: RateLimitedError | None = None


_request_budget: ContextVar[_WaitBudget | None] = ContextVar("rate_limit_request_budget", default=None)


def rate_limit_request(function: Callable[_P, Awaitable[_T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
    """Keep one wait budget across a provider's non-rate transport retries."""
    @wraps(function)
    async def request(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        token = _request_budget.set(_request_budget.get() or _WaitBudget())
        try:
            return await function(*args, **kwargs)
        finally:
            _request_budget.reset(token)
    return request


@dataclass(frozen=True)
class _Admission:
    generation: int
    round: int
    probe: bool


def retry_after_seconds(value: str | None) -> float | None:
    """Normalize an HTTP Retry-After delay/date; never guess from error prose."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class RateLimitScope:
    """Share cooldown/probing across executors drawing on one provider quota.

    Instances are event-loop bound while active. No HTTP status interpretation
    belongs here. Normal operation has no rate cap; executor capacity still
    controls concurrency. Each run/map operation owns a separate wait budget.
    """

    def __init__(self, cooldown_seconds: float = 1.0, max_wait_seconds: float = 600.0) -> None:
        for name, value in (("cooldown_seconds", cooldown_seconds), ("max_wait_seconds", max_wait_seconds)):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.cooldown_seconds = cooldown_seconds
        self.max_wait_seconds = max_wait_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._changed = asyncio.Event()
        self._queue: deque[object] = deque()
        self._generation = 0
        self._round = 0
        self._state = "normal"
        self._until = 0.0
        self._next = 0.0
        self._backoff = min(60.0, self.cooldown_seconds)
        self._active = 0
        self._probe: _Admission | None = None
        self._limit = 1
        self._successes = 0
        self._cause: RateLimitedError | None = None
        # Unknown custom providers use their observed normal-operation high
        # water mark. No new method is required on CapacityProvider.
        self._capacities: WeakKeyDictionary = WeakKeyDictionary()
        self._observed: WeakKeyDictionary = WeakKeyDictionary()
        self._running: WeakKeyDictionary = WeakKeyDictionary()

    def _register(self, owner: object, capacity: object) -> None:
        bound = getattr(capacity, "concurrency", None)
        self._capacities[owner] = bound if isinstance(bound, int) and bound > 0 else None

    def _bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            if not self._loop.is_closed() or self._active or self._probe is not None or self._queue:
                raise RuntimeError("RateLimitScope cannot be shared across event loops")
            self._changed = asyncio.Event()
        self._loop = loop
        return loop

    def _wake(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def _try_normal(self) -> _Admission | None:
        """Atomic fast path, also used after map's lazy capacity acquisition."""
        self._bind()
        if self._state != "normal" or self._queue:
            return None
        self._active += 1
        return _Admission(self._generation, self._round, False)

    async def _acquire(self, budget: _WaitBudget) -> _Admission:
        loop = self._bind()
        immediate = self._try_normal()
        if immediate is not None:
            return immediate
        ticket = object()
        self._queue.append(ticket)
        try:
            while True:
                now = loop.time()
                if self._state != "normal":
                    budget.cause = self._cause
                    if budget.waited >= self.max_wait_seconds:
                        raise RateLimitTimeoutError(
                            "Rate-limit recovery waiting budget exhausted", cause=budget.cause,
                        )
                if self._queue[0] is ticket:
                    if self._state == "normal":
                        break
                    if (self._state == "cooldown" and now >= self._until
                            and self._active == 0 and self._probe is None):
                        self._state = "probe"
                        break
                    if self._state == "recovery" and now >= self._next and self._active < self._limit:
                        break
                changed = self._changed
                limited = self._state != "normal"
                delay = self.max_wait_seconds - budget.waited if limited else None
                if limited and self._queue[0] is ticket:
                    until = self._until if self._state == "cooldown" else self._next
                    if until > now:
                        delay = min(self.max_wait_seconds - budget.waited, until - now)
                started = loop.time()
                try:
                    if delay is None:
                        await changed.wait()
                    else:
                        try:
                            async with asyncio.timeout(delay):
                                await changed.wait()
                        except TimeoutError:
                            pass
                finally:
                    if limited:
                        budget.waited += loop.time() - started
            admission = _Admission(self._generation, self._round, self._state == "probe")
            self._active += 1
            return admission
        finally:
            self._queue.remove(ticket)
            self._wake()

    def _valid(self, admission: _Admission) -> bool:
        return admission.generation == self._generation

    def _can_start(self, admission: _Admission) -> bool:
        """Recheck after capacity waits, immediately before provider invocation.

        Admission only reserves a recovery slot. Only an actual start consumes
        the spacing interval; delayed reservations cannot become a burst.
        """
        if not self._valid(admission):
            return False
        if self._state == "normal":
            return True
        if self._state == "recovery":
            return self._bind().time() >= self._next
        return self._state == "probe" and admission.probe and self._probe is None

    def _started(self, owner: object, admission: _Admission) -> None:
        if self._state == "recovery":
            self._next = self._bind().time() + 1.0 + random.uniform(0, 0.1)
        if admission.probe:
            self._probe = admission
        self._running[owner] = self._running.get(owner, 0) + 1
        if self._state == "normal":
            self._observed[owner] = max(self._observed.get(owner, 1), self._running[owner])

    def _finish(self, admission: _Admission, owner: object, error: BaseException | None,
                *, started: bool) -> None:
        if self._probe is admission:
            self._probe = None
        if started:
            self._running[owner] -= 1
        if self._valid(admission):
            self._active -= 1
        if isinstance(error, RateLimitedError):
            # A stale request may extend cooling, but never release it.
            self._generation += 1
            self._active = 0
            self._cause = error
            delay = error.retry_after
            if delay is None or not math.isfinite(delay) or delay < 0:
                delay = self._backoff
            self._backoff = min(60.0, self._backoff * 2)
            delay = max(min(self.cooldown_seconds, 60.0), delay)
            self._until = max(self._until, self._bind().time() + delay + random.uniform(0, min(delay * .1, 1.0)))
            self._state = "cooldown"
        elif self._valid(admission):
            success = started and error is None
            if admission.probe:
                if success:
                    self._state = "recovery"
                    self._limit = 1
                    self._round += 1
                    self._successes = 0
                    self._next = self._bind().time() + 1.0
                else:
                    # Cancellation/non-rate error cannot strand the probe slot.
                    self._state = "cooldown"
            elif started and self._state == "recovery" and admission.round == self._round:
                self._successes = self._successes + 1 if success else 0
                if self._successes >= self._limit:
                    target = sum(bound or self._observed.get(key, 1) for key, bound in self._capacities.items())
                    if self._limit >= target:
                        self._state = "normal"
                        self._cause = None
                        self._backoff = min(60.0, self.cooldown_seconds)
                    else:
                        self._limit = min(self._limit * 2, target)
                    self._round += 1
                    self._successes = 0
        self._wake()
