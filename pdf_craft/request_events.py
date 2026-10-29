"""Per-attempt provider observations, separate from business adoption and billing."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import inspect
import time
from typing import Any

from .usage_types import RequestUsage


class RequestOutcome(str, Enum):
    SUCCESS = 'success'
    RATE_LIMITED = 'rate_limited'
    SERVER_ERROR = 'server_error'
    REQUEST_REJECTED = 'request_rejected'
    TIMEOUT = 'timeout'
    NETWORK_ERROR = 'network_error'
    INVALID_RESPONSE = 'invalid_response'
    CANCELLED = 'cancelled'
    UNKNOWN_ERROR = 'unknown_error'


class RequestKind(str, Enum):
    MODEL = 'model'
    OCR_SUBMIT = 'ocr_submit'
    OCR_QUERY = 'ocr_query'
    OCR_DOWNLOAD = 'ocr_download'
    OCR_AUTH = 'ocr_auth'


@dataclass(frozen=True)
class RequestEvent:
    """One completed attempt. Provider identity is retained, never serialized here."""
    provider: object = field(repr=False)
    kind: RequestKind
    started_at: datetime
    duration_ms: float
    outcome: RequestOutcome
    usage: RequestUsage | None = None
    http_status: int | None = None
    error_type: str | None = None


# Deliberately synchronous: hand off to the host's own telemetry queue without
# awaiting I/O. No hidden task/queue, callback retry, or event-loop transfer.
RequestCallback = Callable[[RequestEvent], None]


@dataclass
class _Observation:
    http_status: int | None = None
    usage: RequestUsage | None = None
    invalid: bool = False
    timed_out: bool = False


_observation: ContextVar[_Observation | None] = ContextVar('pdf_craft_request_observation', default=None)


def validate_callback(callback: RequestCallback | None) -> None:
    if callback is not None and (not callable(callback) or inspect.iscoroutinefunction(callback)
                                 or inspect.iscoroutinefunction(getattr(callback, '__call__', None))):
        raise TypeError('on_request must be a synchronous, non-blocking callback')


def request_timed_out() -> None:
    current = _observation.get()
    if current is not None:
        current.timed_out = True


def observation_enabled() -> bool:
    return _observation.get() is not None


def observe_response(response: Any) -> None:
    current = _observation.get()
    status = getattr(response, 'status_code', getattr(response, 'status', None))
    if current is not None and isinstance(status, int) and not isinstance(status, bool):
        current.http_status = status


async def response_hook(response: Any) -> None:
    """SDK HTTP hook: preserve headers even if response decoding later fails."""
    observe_response(response)


def observe_request_usage(value: RequestUsage) -> None:
    current = _observation.get()
    if current is not None:
        current.usage = replace(value)


def invalid_response() -> None:
    current = _observation.get()
    if current is not None:
        current.invalid = True


def _result(error: BaseException | None, current: _Observation) -> RequestOutcome:
    chain, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(error)
        if current.http_status is None:
            observe_response(error)
            if current.http_status is None:
                observe_response(getattr(error, 'response', None))
        # An implicit context may be a previous, already-observed retry.
        error = error.__cause__
    if chain and isinstance(chain[0], asyncio.CancelledError) and not current.timed_out:
        return RequestOutcome.CANCELLED
    status = current.http_status
    if status == 429:
        return RequestOutcome.RATE_LIMITED
    if status is not None and 500 <= status < 600:
        return RequestOutcome.SERVER_ERROR
    if status is not None and 400 <= status < 500:
        return RequestOutcome.REQUEST_REJECTED
    if current.timed_out:
        return RequestOutcome.TIMEOUT
    if current.invalid:
        return RequestOutcome.INVALID_RESPONSE
    names = {base.__name__ for item in chain for base in type(item).__mro__}
    if names.intersection({'RateLimitError', 'RateLimitedError', 'TypeSafeRateLimitError'}):
        return RequestOutcome.RATE_LIMITED
    if any('Timeout' in name for name in names):
        return RequestOutcome.TIMEOUT
    if names.intersection({'ConnectionError', 'ConnectError', 'NetworkError', 'ReadError',
                           'WriteError', 'RemoteProtocolError', 'APIConnectionError',
                           'TypeSafeAPIConnectionError'}):
        return RequestOutcome.NETWORK_ERROR
    if names.intersection({'JSONDecodeError', 'ValidationError',
                                               'APIResponseValidationError',
                                               'TypeSafeAPIResponseValidationError'}):
        return RequestOutcome.INVALID_RESPONSE
    return RequestOutcome.UNKNOWN_ERROR if chain else RequestOutcome.SUCCESS


@contextmanager
def observe_request(provider: object, kind: RequestKind = RequestKind.MODEL) -> Iterator[None]:
    callback = getattr(provider, 'on_request', None)
    if callback is None:
        token = _observation.set(None)
        try:
            yield
        finally:
            _observation.reset(token)
        return
    current = _Observation()
    token = _observation.set(current)
    started_at, started = datetime.now(timezone.utc), time.monotonic()
    error = None
    try:
        yield
    except BaseException as caught:
        error = caught
        raise
    finally:
        event = RequestEvent(provider, kind, started_at, (time.monotonic() - started) * 1000,
                             _result(error, current), current.usage, current.http_status,
                             type(error).__name__ if error is not None else None)
        _observation.reset(token)
        # Observer exceptions are not provider errors. Do not log their messages:
        # callbacks can include document content or credentials in exceptions.
        try:
            result = callback(event)
            if inspect.iscoroutine(result):
                result.close()
        except (Exception, asyncio.CancelledError):
            pass
