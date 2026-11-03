"""Explicit readiness of a bounded content group, independent of IO timing.

Members retain their own repair loops and acceptance. A group flushes when
its members are waiting for model results or have finished, rather than after
an arbitrary transport timer. No provider slot is held during collection.
"""
from contextlib import contextmanager
from contextvars import ContextVar


_GROUP: ContextVar['GroupRequests | None'] = ContextVar('translation_group', default=None)


class GroupRequests:
    """Own only the live members of one token-budgeted mapper group."""

    def __init__(self, members):
        self.remaining = members
        self.waiting = 0
        self.flushers = set()

    def flush(self):
        if self.waiting == self.remaining:
            flushers, self.flushers = self.flushers, set()
            for flush in flushers:
                flush()

    @contextmanager
    def member(self):
        token = _GROUP.set(self)
        try:
            yield
        finally:
            _GROUP.reset(token)
            self.remaining -= 1
            self.flush()

    @contextmanager
    def request(self, flush):
        self.flushers.add(flush)
        self.waiting += 1
        self.flush()
        try:
            yield
        finally:
            self.waiting -= 1


def current_group():
    return _GROUP.get()
