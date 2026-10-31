# pylint: disable=unused-argument
"""Official asynchronous JEV service configuration and transport."""

from __future__ import annotations

from .request_events import request_call
from pdf_craft.usage import UsageCollector, request_attempt, observe_tokens
from collections.abc import Mapping

import asyncio
from collections.abc import AsyncIterator, Awaitable, Iterable
from contextlib import asynccontextmanager
from .request_events import validate_callback, RequestCallback, observe_response, invalid_response
from dataclasses import dataclass, field
from typing import Any, Self, Protocol

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient, RetryPolicy, TypeSafeAPIConnectionError,
    TypeSafeAPIError, TypeSafeAPITimeoutError, TypeSafeAuthenticationError,
    TypeSafePermissionDeniedError, TypeSafeRateLimitError,
)

from ._request_headers import extra_header_arguments
from ._policy import current as execution_policy, policy_operation
from .policies import RetryScope, RetryPolicy as BusinessRetryPolicy, FailureLocation

from .rate_limit import rate_limit_request
from .concurrency import (
    AsyncExecutor, NonContinuableError, OperationError, RateLimitedError,
    task_group_error, settle_tasks,
)

@dataclass(frozen=True)
class JEV:
    """Declarative configuration for TypeSafe AI's JEV service."""

    key: str = field(repr=False)
    model: str = "jev-latest"
    url: str = "https://api.typesafe.ai"
    timeout: float | None = 60.0
    on_request: RequestCallback | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        validate_callback(self.on_request)
        if not self.key.strip():
            raise ValueError("JEV API key cannot be empty")
        if not self.model.strip():
            raise ValueError("JEV model cannot be empty")


class _ObservedTransport(httpx2.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await super().handle_async_request(request)
        observe_response(response)
        return response


class JEVRuntime:
    """One event-loop-bound official JEV client reused for a review run."""

    def __init__(self, config: JEV, executor: AsyncExecutor, *, retry_policy: BusinessRetryPolicy | None = None) -> None:
        self._retry_policy = retry_policy or BusinessRetryPolicy()
        self.config = config
        self.executor = executor
        self._client: AsyncTypeSafeClient | None = None

    async def __aenter__(self) -> Self:
        client = AsyncTypeSafeClient(
            api_key=self.config.key,
            model=self.config.model,
            base_url=self.config.url,
            timeout=self.config.timeout,
            retry=RetryPolicy(max_retries=0),
            transport=_ObservedTransport(local_address="0.0.0.0"),
        )
        self._client = client
        await client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(exc_type, exc_value, traceback)

    @policy_operation
    @request_call
    @rate_limit_request
    async def evaluate(
        self,
        page_index: int,
        request: dict[str, Any],
        *,
        _started: asyncio.Event | None = None,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> float:
        return await self._evaluate_with_retry(page_index, request, _started)

    @policy_operation
    async def evaluate_many(
        self, requests: Iterable[tuple[int, dict[str, Any]]],
        *, request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> list[tuple[int, float]]:
        if self._client is None:
            raise RuntimeError("JEVRuntime must be entered before evaluation")
        iterator = iter(enumerate(requests))
        completed: list[tuple[int, int, float]] = []
        tasks: dict[asyncio.Task[float], tuple[int, int, tuple[FailureLocation, ...]]] = {}
        admission: asyncio.Task[bool] | None = None
        admission_owner: asyncio.Task[float] | None = None
        exhausted = False

        def start_next() -> None:
            nonlocal admission, admission_owner, exhausted
            if exhausted:
                return
            try:
                order, (page_index, request) = next(iterator)
            except StopIteration:
                exhausted = True
                return
            started = asyncio.Event()
            task = asyncio.create_task(self.evaluate(
                page_index, request, _started=started,
            ))
            state = request.get('state', {})
            state = state.get('local_raw_evidence', state)
            context_pages = tuple(FailureLocation('page', page_indexes=(part['page_index'],))
                for key in ('previous_page', 'next_page') if isinstance((part := state.get(key)), dict)
                and 'page_index' in part)
            tasks[task] = (order, page_index, context_pages)
            admission_owner = task
            admission = asyncio.create_task(started.wait())

        try:
            start_next()
            while tasks:
                pending: set[asyncio.Task[Any]] = set(tasks)
                if admission is not None:
                    pending.add(admission)
                done, _ = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                fatal = task_group_error(task for task in done if task in tasks)
                if fatal is not None:
                    raise fatal

                owner_finished_before_admission = (
                    admission_owner is not None
                    and admission_owner in done
                    and admission is not None
                    and admission not in done
                )
                admitted = admission is not None and admission in done
                if admitted or owner_finished_before_admission:
                    if admission is not None:
                        admission.cancel()
                        await settle_tasks([admission])
                    admission = None
                    admission_owner = None
                    start_next()

                for task in done:
                    if task not in tasks:
                        continue
                    order, page_index, context_pages = tasks.pop(task)
                    try:
                        probability = task.result()
                        if not 0 <= probability <= 1:
                            raise ValueError('JEV pass probability must be between 0 and 1')
                    except Exception as error:
                        from ._policy import recover
                        from .policies import FailureCategory
                        await recover(error, FailureCategory.ANALYSIS, 'jev_review', 'algorithm_result',
                                      targets=(FailureLocation('page', page_indexes=(page_index,)),),
                                      backend='jev', model=self.config.model, context_locations=context_pages)
                        continue
                    completed.append((order, page_index, probability))
        finally:
            if admission is not None:
                admission.cancel()
            for task in tasks:
                task.cancel()
            teardown: list[asyncio.Task[Any]] = list(tasks)
            if admission is not None:
                teardown.append(admission)
            if teardown:
                await settle_tasks(teardown)
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        return [
            (page_index, probability)
            for _, page_index, probability in sorted(completed)
        ]

    async def _evaluate_once(self, request: dict[str, Any]) -> float:
        assert self._client is not None
        with request_attempt(self.config) as attempt_usage:
            try:
                response = await self._client.system_one(
                    state=request["state"],
                    questions=request["questions"],
                    **extra_header_arguments(),
                )
            except OperationError:
                raise
            except Exception as error:
                normalized = _provider_error(error)
                if normalized is error:
                    raise
                raise normalized from error
            observe_tokens(getattr(response, 'usage', None))
            try:
                names = request["questions"]
                if not names:
                    raise ValueError('JEV evaluation requires at least one question')
                probabilities = [float(response.nouls[name].noul) for name in names]
                if any(not 0 <= probability <= 1 for probability in probabilities):
                    raise ValueError('JEV pass probability must be between 0 and 1')
                probability = min(probabilities)
            except (ValueError, TypeError, KeyError, AttributeError):
                invalid_response()
                raise
            if attempt_usage is not None:
                attempt_usage.accept()
            return probability

    async def _evaluate_with_retry(
        self,
        page_index: int,
        request: dict[str, Any],
        started: asyncio.Event | None = None,
    ) -> float:
        del page_index
        try:
            async def invoke() -> float:
                if started is not None:
                    started.set()
                return await self._evaluate_once(request)

            return await self.executor.run(invoke)
        except NonContinuableError:
            raise
        except OperationError as error:
            error.attempts = 1
            if not _is_retryable(error) or (execution_policy().retry.attempts(RetryScope.ANALYSIS) - 1) == 0:
                raise
            return await self._retry_after_error(
                request, error, attempts_used=1,
            )

    async def _retry_after_error(
        self,
        request: dict[str, Any],
        error: OperationError,
        *,
        attempts_used: int,
    ) -> float:
        current = error
        while attempts_used <= (execution_policy().retry.attempts(RetryScope.ANALYSIS) - 1):
            delay = 0.5  # Non-rate transport retry; limiting belongs to the executor.
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                return await self.executor.run(
                    lambda: self._evaluate_once(request),
                )
            except NonContinuableError:
                raise
            except OperationError as next_error:
                current = next_error
                attempts_used += 1
                if not _is_retryable(current):
                    raise
        current.attempts = attempts_used
        raise current


class JEVRequest(Protocol):
    def __call__(
        self, page_index: int, request: dict[str, Any], *,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> Awaitable[float]: ...


@asynccontextmanager
async def create_jev_request(
    config: JEV, executor: AsyncExecutor, *, retry_policy: BusinessRetryPolicy | None = None,
) -> AsyncIterator[JEVRequest]:
    """Bind JEV configuration and capacity into a retrying request function."""
    async with JEVRuntime(config, executor, retry_policy=retry_policy) as runtime:
        yield runtime.evaluate


def _provider_error(error: Exception) -> OperationError:
    if isinstance(error, OperationError):
        return error
    status = getattr(error, "status", None)
    if isinstance(error, TypeSafeRateLimitError) or status == 429:
        retry_after_ms = getattr(error, "retry_after_ms", None)
        retry_after = retry_after_ms / 1000 if retry_after_ms is not None else None
        return RateLimitedError(retry_after=retry_after, cause=error)
    if isinstance(
        error, (TypeSafeAuthenticationError, TypeSafePermissionDeniedError),
    ) or status in (401, 402, 403):
        return NonContinuableError(
            "The JEV provider cannot continue serving requests.", cause=error,
        )
    return OperationError(str(error) or type(error).__name__, cause=error)


def _is_retryable(error: OperationError) -> bool:
    cause = error.__cause__
    if isinstance(cause, (TypeSafeAPIConnectionError, TypeSafeAPITimeoutError)):
        return True
    status = getattr(cause, "status", None)
    return isinstance(cause, TypeSafeAPIError) and isinstance(status, int) and (
        status == 408 or status >= 500
    )
