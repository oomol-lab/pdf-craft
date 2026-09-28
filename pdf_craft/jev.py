"""Official asynchronous JEV service configuration and transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Self

import httpx2
from typesafe_sdk import (
    AsyncTypeSafeClient, RetryPolicy, TypeSafeAPIConnectionError,
    TypeSafeAPIError, TypeSafeAPITimeoutError, TypeSafeAuthenticationError,
    TypeSafePermissionDeniedError, TypeSafeRateLimitError,
)

from .concurrency import (
    AsyncExecutor, NonContinuableError, OperationError, RateLimitedError,
    task_group_fatal_error,
)


@dataclass(frozen=True)
class JEV:
    """Declarative configuration for TypeSafe AI's JEV service."""

    key: str = field(repr=False)
    model: str = "jev-latest"
    url: str = "https://api.typesafe.ai"
    timeout: float | None = 60.0
    retry_times: int = 2

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("JEV API key cannot be empty")
        if not self.model.strip():
            raise ValueError("JEV model cannot be empty")
        if self.retry_times < 0:
            raise ValueError("JEV retry_times cannot be negative")


class JEVRuntime:
    """One event-loop-bound official JEV client reused for a review run."""

    def __init__(self, config: JEV, executor: AsyncExecutor) -> None:
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
            transport=httpx2.AsyncHTTPTransport(local_address="0.0.0.0"),
        )
        self._client = client
        await client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(exc_type, exc_value, traceback)

    async def evaluate(self, page_index: int, request: dict[str, Any]) -> float:
        return await self._evaluate_with_retry(page_index, request)

    async def evaluate_many(
        self, requests: Iterable[tuple[int, dict[str, Any]]],
    ) -> list[tuple[int, float]]:
        if self._client is None:
            raise RuntimeError("JEVRuntime must be entered before evaluation")
        def initial_requests():
            for order, (page_index, request) in enumerate(requests):
                yield order, page_index, request

        completed: list[tuple[int, int, float]] = []
        scheduled: dict[int, tuple[int, int, dict[str, Any]]] = {}
        retries: dict[asyncio.Task[float], tuple[int, int]] = {}

        def operations():
            for order, page_index, request in initial_requests():
                async def invoke(
                    operation_id: int,
                    order=order,
                    page_index=page_index,
                    request=request,
                ):
                    scheduled[operation_id] = (order, page_index, request)
                    probability = await self._evaluate_once(request)
                    return operation_id, probability
                yield invoke

        try:
            async with aclosing(self.executor.map(operations())) as results:
                async for result in results:
                    order, page_index, request = scheduled.pop(result.operation_id)
                    if result.succeeded:
                        assert result.value is not None
                        completed.append((order, page_index, result.value))
                        continue
                    error = result.error
                    assert error is not None
                    if _is_retryable(error) and self.config.retry_times > 0:
                        task = asyncio.create_task(self._retry_after_error(
                            request, error, attempts_used=1,
                        ))
                        retries[task] = (order, page_index)
                        continue
                    raise error
            while retries:
                done, _ = await asyncio.wait(
                    retries, return_when=asyncio.FIRST_COMPLETED,
                )
                fatal = task_group_fatal_error(done)
                if fatal is not None:
                    raise fatal
                for task in done:
                    order, page_index = retries.pop(task)
                    completed.append((order, page_index, task.result()))
        finally:
            for task in retries:
                task.cancel()
            if retries:
                await asyncio.gather(*retries, return_exceptions=True)
        return [
            (page_index, probability)
            for _, page_index, probability in sorted(completed)
        ]

    async def _evaluate_once(self, request: dict[str, Any]) -> float:
        assert self._client is not None
        try:
            response = await self._client.system_one(
                state=request["state"],
                questions=request["questions"],
            )
        except Exception as error:
            raise _provider_error(error) from error
        return float(response.nouls["page_passes_strict_standard"].noul)

    async def _evaluate_with_retry(
        self, page_index: int, request: dict[str, Any],
    ) -> float:
        del page_index
        try:
            return await self.executor.run(lambda: self._evaluate_once(request))
        except NonContinuableError:
            raise
        except OperationError as error:
            if not _is_retryable(error) or self.config.retry_times == 0:
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
        while attempts_used <= self.config.retry_times:
            delay = (
                current.retry_after
                if isinstance(current, RateLimitedError)
                and current.retry_after is not None
                else 0.5
            )
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
        raise current


JEVRequest = Callable[[int, dict[str, Any]], Awaitable[float]]


@asynccontextmanager
async def create_jev_request(
    config: JEV, executor: AsyncExecutor,
) -> AsyncIterator[JEVRequest]:
    """Bind JEV configuration and capacity into a retrying request function."""
    async with JEVRuntime(config, executor) as runtime:
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
    if isinstance(error, RateLimitedError):
        return True
    cause = error.__cause__
    if isinstance(cause, (TypeSafeAPIConnectionError, TypeSafeAPITimeoutError)):
        return True
    status = getattr(cause, "status", None)
    return isinstance(cause, TypeSafeAPIError) and isinstance(status, int) and (
        status == 408 or status >= 500
    )
