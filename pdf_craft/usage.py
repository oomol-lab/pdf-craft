# pylint: disable=protected-access,attribute-defined-outside-init
"""Operation-local, provider-identity accounting for observed remote consumption."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from threading import RLock

from .usage_types import RequestUsage
from .request_events import (RequestKind, observe_request, observation_enabled, observe_request_usage)
from typing import Any, TypeVar, cast




@dataclass
class Usage:
    """Consumption adopted by the business operation, and discarded attempts."""
    effective: RequestUsage = field(default_factory=RequestUsage)
    wasted: RequestUsage = field(default_factory=RequestUsage)


class UsageReport(Mapping[object, Usage]):
    """Detached snapshot indexed by provider object identity, including unhashable configs."""
    def __init__(self, entries: Iterable[tuple[int, tuple[object, Usage]]]):
        self._entries = dict(entries)

    def __getitem__(self, provider: object) -> Usage:
        return self._entries[id(provider)][1]

    def __iter__(self) -> Iterator[object]:
        return (provider for provider, _ in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


class UsageCollector:
    """Pass one collector per task; report() is available even after failure/cancellation."""
    def __init__(self) -> None:
        self._entries: dict[int, tuple[object, Usage]] = {}
        self._lock = RLock()
        self._pending: set[UsageScope] = set()

    def _record(self, provider: object, value: RequestUsage, accepted: bool) -> None:
        with self._lock:
            _, usage = self._entries.setdefault(id(provider), (provider, Usage()))
            (usage.effective if accepted else usage.wasted)._add(value)

    def report(self) -> UsageReport:
        """Return an independent snapshot; provider keys retain their original identity."""
        with self._lock:
            return UsageReport((key, (provider, Usage(replace(value.effective), replace(value.wasted))))
                               for key, (provider, value) in self._entries.items())


_collector: ContextVar[UsageCollector | None] = ContextVar('pdf_craft_usage', default=None)
_candidate: ContextVar[UsageScope | None] = ContextVar('pdf_craft_usage_candidate', default=None)
_attempt: ContextVar[RequestUsage | None] = ContextVar('pdf_craft_usage_attempt', default=None)


@contextmanager
def usage_context(collector: UsageCollector | None):
    """Inherit an operation's collector, unless explicitly replaced."""
    if collector is None or collector is _collector.get():
        yield
        return
    if not isinstance(collector, UsageCollector):
        raise TypeError('usage must be UsageCollector or None')
    token = _collector.set(collector)
    candidate = _candidate.set(None)
    try:
        yield
    finally:
        for pending in tuple(collector._pending):
            pending.settle(False)
        _candidate.reset(candidate)
        _collector.reset(token)


class UsageScope:
    """Aggregate only a live candidate's resources until its validator adopts it."""
    def __init__(self, provider: object | None = None):
        self.provider = provider
        self.collector = _collector.get()
        parent = _candidate.get()
        self.parent = parent if parent is not None and parent.collector is self.collector else None
        self.pending: dict[int, tuple[object, RequestUsage]] = {}
        self.accepted = False
        self.deferred = False
        self._lock = RLock()

    def __enter__(self):
        self._token = _candidate.set(self)
        return self

    def accept(self) -> None:
        self.accepted = True

    def add(self, provider, value):
        if self.provider is not None and provider is not self.provider:
            if self.parent is not None:
                self.parent.add(provider, value)
            elif self.collector is not None:
                self.collector._record(provider, value, True)
            return
        with self._lock:
            _, total = self.pending.setdefault(id(provider), (provider, RequestUsage()))
            total._add(value)

    def defer(self):
        self.deferred = True
        if self.collector is not None:
            self.collector._pending.add(self)
        return self

    def __exit__(self, kind, _value, _traceback):
        _candidate.reset(self._token)
        if self.deferred and kind is None:
            return
        self.settle(kind is None and self.accepted)

    def settle(self, accepted: bool):
        if self.collector is not None:
            self.collector._pending.discard(self)
        for provider, value in self.pending.values():
            if self.collector is not None:
                if accepted and self.parent is not None:
                    self.parent.add(provider, value)
                else:
                    self.collector._record(provider, value, accepted)
        self.pending.clear()


@contextmanager
def request_attempt(provider, kind: RequestKind = RequestKind.MODEL):
    """Collect each real attempt independently of later business adoption."""
    with observe_request(provider, kind):
        collector = _collector.get()
        if collector is None and not observation_enabled():
            token = _attempt.set(None)
            try:
                yield None
            finally:
                _attempt.reset(token)
            return
        value = RequestUsage(requests=1)
        token = _attempt.set(value)
        with UsageScope() as scope:
            try:
                yield scope
            finally:
                scope.add(provider, value)
                _attempt.reset(token)


def observe_tokens(data: Any) -> None:
    """Keep reported totals only. Missing/invalid fields are ignored, never estimated."""
    value = _attempt.get()
    if value is None or data is None:
        return
    def get(source, key, default=None):
        return source.get(key, default) if isinstance(source, dict) else getattr(source, key, default)
    def count(number):
        return number if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else None
    incoming = count(get(data, 'prompt_tokens', get(data, 'input_tokens')))
    outgoing = count(get(data, 'completion_tokens', get(data, 'output_tokens')))
    details = get(data, 'prompt_tokens_details', get(data, 'input_tokens_details'))
    cached = count(get(details, 'cached_tokens',
        get(data, 'prompt_cache_hit_tokens', get(data, 'cached_input_tokens'))))
    if incoming is not None:
        value.input_tokens = incoming
    if outgoing is not None:
        value.output_tokens = outgoing
    if cached is not None:
        value.cached_input_tokens = cached
    value.cached_input_tokens = min(value.input_tokens, value.cached_input_tokens)
    if any(number is not None for number in (incoming, outgoing, cached)):
        observe_request_usage(value)


def usage_enabled() -> bool:
    return _collector.get() is not None or observation_enabled()


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def adopted_result(accept: Callable[[Any], bool] = lambda value: value is not None) -> Callable[[F], F]:
    """Keep a candidate tentative through its adapter's decoding and validation."""
    from functools import wraps
    def decorate(function: F) -> F:
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with UsageScope() as scope:
                result = await function(*args, **kwargs)
                if accept(result):
                    scope.accept()
                return result
        return cast(F, wrapped)
    return decorate
