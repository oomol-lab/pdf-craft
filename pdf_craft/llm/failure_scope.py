"""Optional adoption ownership for successful repair-loop evidence."""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .failure_log import FailureLog

_OWNER: ContextVar['FailureScope | None'] = ContextVar('llm_failure_owner', default=None)


def active_failure_scope() -> 'FailureScope | None':
    return _OWNER.get()


class FailureScope:
    """Own disk-backed logs until the result has actually been committed."""

    def __init__(self):
        self.logs: list[FailureLog] = []

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _OWNER.set(self)
        try:
            yield
        finally:
            _OWNER.reset(token)

    def add(self, log: FailureLog) -> None:
        if log.path is not None:
            # Successful attempts have already been spooled. Do not retain a
            # whole XML answer in RAM while waiting for ordered submission.
            log.record.clear()
            self.logs.append(log)

    async def close(self, error: BaseException | None = None) -> None:
        logs, self.logs = self.logs, []
        for log in logs:
            try:
                if error is not None:
                    await log.failed(error)
            finally:
                await log.close()
