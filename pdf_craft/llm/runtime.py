# pylint: disable=protected-access
"""Native asynchronous LLM transport with a synchronous compatibility edge."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import threading
import uuid
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Self, cast

import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam

from ..runtime import IO_DOMAIN, run_sync
from ..concurrency import (
    AsyncExecutor, NonContinuableError, OperationError, RateLimitedError,
)
from .core import LLM
from .error import is_retry_error
from .increasable import Increasable
from .types import Message, MessageRole

_CACHE_LOCK = threading.Lock()
_LOGGER_LOCK = threading.Lock()


class LLMTransportError(RuntimeError):
    def __init__(self, message: str, *, attempts: int, cause: Exception) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.__cause__ = cause


class LLMEmptyResponseError(RuntimeError):
    def __init__(self, *, attempts: int) -> None:
        super().__init__(f"LLM returned an empty response after {attempts} attempt(s)")
        self.attempts = attempts


class LLMRuntime:
    """Provider runtime whose network, streaming, retry and limiter are async."""

    def __init__(
        self,
        config: LLM,
        *,
        executor: AsyncExecutor,
        protocol_version: str = "1",
    ) -> None:
        self.config = config
        self.executor = executor
        self.protocol_version = protocol_version
        self._top_p, self._temperature = Increasable(config.top_p), Increasable(config.temperature)
        self._logger: logging.Logger | None = None

    def context(self, cache_seed_content: str | None = None) -> "LLMContext":
        return LLMContext(self, cache_seed_content)

    async def request(
        self, input: str | list[Message], max_tokens: int | None = None,
        temperature: float | None = None, top_p: float | None = None, *,
        cache_seed_content: str | None = None, retry_index: int | None = None,
        retry_max: int | None = None, use_cache: bool = True,
    ) -> str:
        async with self.context(cache_seed_content) as context:
            return await context.request(
                input, max_tokens, temperature, top_p,
                retry_index=retry_index, retry_max=retry_max, use_cache=use_cache,
            )

    def _request_blocking(self, *args, **kwargs) -> str:
        return run_sync(self.request(*args, **kwargs))

    @staticmethod
    def _scheduled(value, source: Increasable, index, maximum):
        if value is not None:
            return value
        value_range = source._value_range
        if index is not None and maximum and value_range is not None:
            start, end = value_range
            return start + (end - start) * min(max(index, 0), maximum) / maximum
        return source.context().current

    async def _invoke_async(
        self, messages: list[Message], max_tokens, temperature, top_p, *,
        force_ipv4: bool = False,
    ) -> str:
        converted = cast(list[ChatCompletionMessageParam], [
            {"role": message.role.name.lower(), "content": message.message} for message in messages
        ])
        return await self._invoke_stream(
            converted, max_tokens, temperature, top_p, force_ipv4=force_ipv4,
        )

    async def _invoke_stream(
        self, messages: list[ChatCompletionMessageParam], max_tokens,
        temperature, top_p, *, force_ipv4: bool,
    ) -> str:
        http_client = (
            httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"))
            if force_ipv4 else None
        )
        # Scope the client to its event loop. This remains safe when the
        # legacy sync adapter is used by a dedicated translation worker.
        async with openai.AsyncOpenAI(
            api_key=self.config.key, base_url=self.config.url,
            timeout=self.config.timeout, max_retries=0, http_client=http_client,
        ) as client:
            stream = await client.chat.completions.create(
                model=self.config.model, messages=messages, stream=True,
                top_p=top_p, temperature=temperature, max_tokens=max_tokens,
            )
            parts: list[str] = []
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    parts.append(chunk.choices[0].delta.content)
            return "".join(parts)

    async def _invoke_for_request(
        self, messages, max_tokens, temperature, top_p, *, force_ipv4: bool,
    ) -> str:
        # Preserve the established test/custom transport seam. Production has
        # no synchronous _invoke member and always takes _invoke_async above.
        override = self.__dict__.get("_invoke")
        if override is None:
            return await self._invoke_async(
                messages, max_tokens, temperature, top_p,
                force_ipv4=force_ipv4,
            )
        result = override(messages, max_tokens, temperature, top_p)
        if inspect.isawaitable(result):
            return await result
        return cast(str, result)


class LLMContext(AbstractAsyncContextManager["LLMContext"]):
    def __init__(self, runtime: LLMRuntime, cache_seed_content: str | None) -> None:
        self.runtime, self.cache_seed_content = runtime, cache_seed_content
        self.context_id, self._pending = uuid.uuid4().hex[:12], set()
        self._top_p, self._temperature = runtime._top_p.context(), runtime._temperature.context()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        _commit_pending(self._pending, exc_type)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await IO_DOMAIN.run(_commit_pending, self._pending, exc_type)

    async def request(
        self, input, max_tokens=None, temperature=None, top_p=None, *,
        retry_index=None, retry_max=None, use_cache=True,
    ) -> str:
        messages = [Message(MessageRole.USER, input)] if isinstance(input, str) else list(input)
        temperature = self.runtime._scheduled(
            temperature, self.runtime._temperature, retry_index, retry_max,
        )
        top_p = self.runtime._scheduled(top_p, self.runtime._top_p, retry_index, retry_max)
        key = self._cache_key(messages, max_tokens, temperature, top_p) if use_cache else None
        cache_path = self.runtime.config.cache_path
        if key and cache_path:
            cached = cache_path / f"{key}.txt"
            cached_text = await IO_DOMAIN.run(_read_cached, cached)
            if cached_text is not None:
                await self._log_async("cache-hit", 0, key=key)
                return cached_text
        await self._log_async("cache-miss", 0, key=key)
        last_error: Exception | None = None
        empty_attempts = 0
        try:
            attempt = 0
            force_ipv4 = False
            while attempt <= self.runtime.config.retry_times:
                try:
                    await self._log_async("request", attempt + 1, key=key)
                    async def invoke_once() -> str:
                        try:
                            return await self.runtime._invoke_for_request(
                                messages, max_tokens, temperature, top_p,
                                force_ipv4=force_ipv4,
                            )
                        except Exception as error:
                            raise _provider_error(error) from error

                    response = await self.runtime.executor.run(invoke_once)
                    if not response.strip():
                        empty_attempts += 1
                        await self._log_async("empty-response", attempt + 1, key=key)
                        if attempt >= self.runtime.config.retry_times:
                            raise LLMEmptyResponseError(attempts=empty_attempts)
                        attempt += 1
                        force_ipv4 = False
                        continue
                    if key and cache_path:
                        temporary = cache_path / f"{key}.{self.context_id}.txt"
                        await IO_DOMAIN.run(_write_cached, temporary, response)
                        self._pending.add(temporary)
                    await self._log_async("success", attempt + 1, key=key)
                    return response
                except NonContinuableError:
                    raise
                except OperationError as error:
                    last_error = error
                    cause = error.__cause__
                    if (
                        not force_ipv4
                        and isinstance(cause, BaseException)
                        and _caused_by_connect_error(cause)
                    ):
                        # This is another remote operation, so the loop leaves
                        # the executor before reacquiring capacity for IPv4.
                        force_ipv4 = True
                        continue
                    retryable = isinstance(error, RateLimitedError) or (
                        isinstance(cause, Exception) and is_retry_error(cause)
                    )
                    await self._log_async(
                        "transport-error" if retryable else "non-retryable-error",
                        attempt + 1, key=key, error=error,
                    )
                    if not retryable or attempt >= self.runtime.config.retry_times:
                        final_cause = (
                            error.__cause__
                            if isinstance(error.__cause__, Exception)
                            else error
                        )
                        raise LLMTransportError(
                            "LLM transport request failed",
                            attempts=attempt + 1,
                            cause=final_cause,
                        ) from final_cause
                    rate_limit = error if isinstance(error, RateLimitedError) else None
                    delay = (
                        rate_limit.retry_after
                        if rate_limit is not None and rate_limit.retry_after is not None
                        else self.runtime.config.retry_interval_seconds
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    attempt += 1
                    force_ipv4 = False
        finally:
            self._temperature.increase()
            self._top_p.increase()
        raise RuntimeError("LLM request failed") from last_error

    def _request_blocking(self, *args, **kwargs) -> str:
        return run_sync(self.request(*args, **kwargs))

    def _cache_key(self, messages, max_tokens, temperature, top_p) -> str:
        payload = {
            "url": self.runtime.config.url, "model": self.runtime.config.model,
            "messages": [(m.role.name, m.message) for m in messages],
            "seed": self.cache_seed_content, "temperature": temperature,
            "top_p": top_p, "max_tokens": max_tokens,
            "protocol": self.runtime.protocol_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    async def _log_async(
        self, category: str, attempt: int, *, key: str | None,
        error: Exception | None = None,
    ) -> None:
        await IO_DOMAIN.run(self._log, category, attempt, key=key, error=error)

    def _log(
        self, category: str, attempt: int, *, key: str | None,
        error: Exception | None = None,
    ) -> None:
        with _LOGGER_LOCK:
            if self.runtime._logger is None:
                self.runtime._logger = _create_logger(self.runtime.config.log_dir_path)
            logger = self.runtime._logger
            logger.info(json.dumps({
                "session": self.context_id, "category": category, "attempt": attempt,
                "model": self.runtime.config.model, "cache_key": key,
                **({"error": type(error).__name__} if error else {}),
            }, ensure_ascii=False))
            _close_file_handlers(logger)


def runtime_for(
    config: LLM,
    executor: AsyncExecutor,
    *,
    protocol_version: str = "1",
) -> LLMRuntime:
    return LLMRuntime(
        config,
        protocol_version=protocol_version,
        executor=executor,
    )


def create_llm_request(
    config: LLM,
    executor: AsyncExecutor,
    *,
    protocol_version: str = "1",
) -> Callable[..., Awaitable[str]]:
    """Bind an LLM config and executor into a retrying request function."""
    return runtime_for(
        config, executor, protocol_version=protocol_version,
    ).request


def _provider_error(error: Exception) -> OperationError:
    if isinstance(error, OperationError):
        return error
    status = getattr(error, "status_code", None)
    if status == 429 or isinstance(error, openai.RateLimitError):
        if _is_quota_error(error):
            return NonContinuableError(
                "The LLM provider quota is exhausted.", cause=error,
            )
        response = getattr(error, "response", None)
        raw_retry_after = (
            response.headers.get("retry-after") if response is not None else None
        )
        try:
            retry_after = float(raw_retry_after) if raw_retry_after is not None else None
        except (TypeError, ValueError):
            retry_after = None
        return RateLimitedError(retry_after=retry_after, cause=error)
    if status in (401, 402, 403) or isinstance(
        error, (openai.AuthenticationError, openai.PermissionDeniedError),
    ):
        return NonContinuableError(
            "The LLM provider cannot continue serving requests.", cause=error,
        )
    return OperationError(str(error) or type(error).__name__, cause=error)


def _is_quota_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if response is None:
        return False
    try:
        body = response.json()
    except (AttributeError, ValueError):
        return False
    if not isinstance(body, dict):
        return False
    provider_error = body.get("error")
    if not isinstance(provider_error, dict):
        return False
    terminal = {"insufficient_quota", "quota_exceeded", "billing_not_active"}
    code = str(provider_error.get("code") or "").lower()
    error_type = str(provider_error.get("type") or "").lower()
    return code in terminal or error_type in terminal


def _caused_by_connect_error(error: BaseException) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, httpx.ConnectError):
            return True
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _read_cached(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _write_cached(path: Path, response: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response, encoding="utf-8")


def _commit_pending(pending: set[Path], exc_type) -> None:
    for temporary in sorted(pending):
        if exc_type is None:
            permanent = temporary.with_name(temporary.name.rsplit(".", 2)[0] + ".txt")
            with _CACHE_LOCK:
                if permanent.exists():
                    temporary.unlink(missing_ok=True)
                else:
                    temporary.rename(permanent)
        else:
            temporary.unlink(missing_ok=True)


def _create_logger(path):
    logger = logging.getLogger(f"pdf_craft.llm.{uuid.uuid4().hex}")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if path is not None:
        path.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path / f"request-{uuid.uuid4().hex}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def _close_file_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.close()
