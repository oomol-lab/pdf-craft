# pylint: disable=unused-argument
# pylint: disable=protected-access
"""Native asynchronous LLM transport."""

from __future__ import annotations

from pdf_craft.usage import UsageCollector
import asyncio
import hashlib
import inspect
import json
import logging
import threading
import time
import uuid
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from collections.abc import Mapping, Awaitable, Callable
from typing import Self, cast

import httpx
import openai
from jinja2 import Template
from tiktoken import Encoding, get_encoding
from openai.types.chat import ChatCompletionMessageParam

from .._policy import policy_operation
from ..usage import request_attempt, observe_tokens, usage_enabled as _usage_enabled
from .._request_headers import extra_header_arguments
from ..runtime import IO_DOMAIN
from ..rate_limit import RateLimitTimeoutError, rate_limit_request, retry_after_seconds
from ..concurrency import (
    AsyncExecutor, NonContinuableError, OperationError, RateLimitedError, fatal_error_boundary,
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

    async def encoding(self) -> Encoding:
        """Load tokenizer resources outside the event loop."""
        return await IO_DOMAIN.run(_load_encoding, self.config)

    async def template(self, name: str) -> Template:
        """Read and compile a prompt on the filesystem domain."""
        return await IO_DOMAIN.run(_load_template, self.config, name)

    def context(self, cache_seed_content: str | None = None) -> "LLMContext":
        return LLMContext(self, cache_seed_content)

    @policy_operation
    @fatal_error_boundary
    async def request(
        self, input: str | list[Message], max_tokens: int | None = None,
        temperature: float | None = None, top_p: float | None = None, *,
        cache_seed_content: str | None = None, retry_index: int | None = None,
        retry_max: int | None = None, use_cache: bool = True,
        deadline: float | None = None,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
    ) -> str:
        async with self.context(cache_seed_content) as context:
            return await context.request(
                input, max_tokens, temperature, top_p,
                retry_index=retry_index, retry_max=retry_max, use_cache=use_cache,
                deadline=deadline,
            )

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
        # Scope the client to its event loop.
        stream = None
        client = openai.AsyncOpenAI(
            api_key=self.config.key, base_url=self.config.url,
            timeout=self.config.timeout, max_retries=0, http_client=http_client,
        )
        try:
            await client.__aenter__()
            stream = await client.chat.completions.create(
                model=self.config.model, messages=messages, stream=True,
                top_p=top_p, temperature=temperature, max_tokens=max_tokens,
                stream_options={"include_usage": True} if _usage_enabled() else openai.omit,
                **extra_header_arguments(),
            )
            parts: list[str] = []
            async for chunk in stream:
                observe_tokens(getattr(chunk, "usage", None))
                if chunk.choices and chunk.choices[0].delta.content:
                    parts.append(chunk.choices[0].delta.content)
            return "".join(parts)
        finally:
            # Separate allowances ensure a stalled stream close cannot prevent
            # closing its owning HTTP client. No detached cleanup task is used.
            try:
                close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
                if close is not None:
                    await self._close_resource(close)
            finally:
                await self._close_resource(lambda: client.__aexit__(None, None, None))

    async def _close_resource(self, close) -> None:
        try:
            async with asyncio.timeout(self.config.cleanup_timeout / 2):
                result = close()
                if inspect.isawaitable(result):
                    await result
        except TimeoutError as error:
            # The transport task has stopped before its capacity is released.
            # Uncooperative extensions that swallow cancellation need isolation
            # in the host process; asyncio cannot forcibly kill a coroutine.
            await self.context()._log_async("cleanup-timeout", 0, key=None, error=error)

    async def _invoke_for_request(
        self, messages, max_tokens, temperature, top_p, *, force_ipv4: bool,
    ) -> str:
        # Preserve the test/custom transport seam while keeping its contract async.
        override = self.__dict__.get("_invoke")
        if override is None:
            return await self._invoke_async(
                messages, max_tokens, temperature, top_p,
                force_ipv4=force_ipv4,
            )
        return await override(messages, max_tokens, temperature, top_p)


class LLMContext(AbstractAsyncContextManager["LLMContext"]):
    def __init__(self, runtime: LLMRuntime, cache_seed_content: str | None) -> None:
        self.runtime, self.cache_seed_content = runtime, cache_seed_content
        self.context_id, self._pending = uuid.uuid4().hex[:12], set()
        self._top_p, self._temperature = runtime._top_p.context(), runtime._temperature.context()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await IO_DOMAIN.run(_commit_pending, self._pending, exc_type)

    @policy_operation
    @fatal_error_boundary
    @rate_limit_request
    async def request(
        self, input, max_tokens=None, temperature=None, top_p=None, *,
        retry_index=None, retry_max=None, use_cache=True, deadline=None,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
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
        last_error: BaseException | None = None
        empty_attempts = 0
        if deadline is None and self.runtime.config.total_timeout is not None:
            deadline = time.monotonic() + self.runtime.config.total_timeout

        async def raise_timeout(attempt: int) -> None:
            error = asyncio.TimeoutError()
            await self._log_async("timeout", attempt, key=key, error=error)
            raise LLMTransportError(
                "LLM request exceeded its total timeout",
                attempts=attempt,
                cause=error,
            ) from error

        attempt = 0
        transport_attempts = 0
        try:
            force_ipv4 = False
            while attempt <= self.runtime.config.retry_times:
                try:
                    await self._log_async("request", attempt + 1, key=key)
                    async def invoke_once() -> str:
                        nonlocal transport_attempts
                        transport_attempts += 1
                        try:
                            with request_attempt(self.runtime.config) as attempt_usage:
                                text = await self.runtime._invoke_for_request(
                                    messages, max_tokens, temperature, top_p,
                                    force_ipv4=force_ipv4,
                                )
                                if text.strip() and attempt_usage is not None:
                                    attempt_usage.accept()
                                return text
                        except OperationError:
                            raise
                        except Exception as error:
                            normalized = _provider_error(error)
                            if normalized is error:
                                raise
                            raise normalized from error

                    if deadline is None:
                        response = await self.runtime.executor.run(invoke_once)
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            await raise_timeout(attempt + 1)
                        try:
                            # Keep transport and cleanup on the caller task.
                            # Python 3.11 wait_for can detach its child when the
                            # caller is cancelled again during child teardown.
                            async with asyncio.timeout(remaining):
                                response = await self.runtime.executor.run(invoke_once)
                        except asyncio.TimeoutError as error:
                            await self._log_async(
                                "timeout", attempt + 1, key=key, error=error,
                            )
                            raise LLMTransportError(
                                "LLM request exceeded its total timeout",
                                attempts=transport_attempts,
                                cause=error,
                            ) from error
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
                except (NonContinuableError, RateLimitTimeoutError):
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
                    retryable = isinstance(cause, Exception) and is_retry_error(cause)
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
                            attempts=transport_attempts,
                            cause=final_cause,
                        ) from final_cause
                    delay = self.runtime.config.retry_interval_seconds
                    await self._log_async("retry", attempt + 1, key=key, error=error)
                    if delay > 0:
                        if deadline is None:
                            await asyncio.sleep(delay)
                        else:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                await raise_timeout(attempt + 1)
                            try:
                                async with asyncio.timeout(remaining):
                                    await asyncio.sleep(delay)
                            except asyncio.TimeoutError:
                                await raise_timeout(attempt + 1)
                    attempt += 1
                    force_ipv4 = False
        except BaseException as error:
            await self._log_async(
                "cancelled" if isinstance(error, asyncio.CancelledError) else "failure",
                attempt + 1, key=key, error=error,
            )
            raise
        finally:
            self._temperature.increase()
            self._top_p.increase()
        raise RuntimeError("LLM request failed") from last_error

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
        error: BaseException | None = None,
    ) -> None:
        await IO_DOMAIN.run(self._log, category, attempt, key=key, error=error)

    def _log(
        self, category: str, attempt: int, *, key: str | None,
        error: BaseException | None = None,
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
    rate_code = getattr(error, "code", None)
    rate_type = getattr(error, "type", None)
    if (status == 429 or isinstance(error, openai.RateLimitError)
            or rate_code == "rate_limit_exceeded" or rate_type == "rate_limit_error"):
        if _is_quota_error(error):
            return NonContinuableError(
                "The LLM provider quota is exhausted.", cause=error,
            )
        response = getattr(error, "response", None)
        raw_retry_after = (
            response.headers.get("retry-after") if response is not None else None
        )
        retry_after = retry_after_seconds(raw_retry_after)
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
    logger.propagate = path is None
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


def _load_encoding(config: LLM) -> Encoding:
    with config._resource_lock:
        if config._encoding is None:
            config._encoding = get_encoding(config.token_encoding)
        return config._encoding

def _load_template(config: LLM, template_name: str) -> Template:
    template = config._templates.get(template_name)
    if template is None:
        with config._resource_lock:
            template = config._templates.get(template_name)
            if template is None:
                path = Path(__file__).parent.parent / "transformer" / "xml_translator" / "data" / f"{template_name}.jinja"
                template = Template(path.read_text(encoding="utf-8"))
                config._templates[template_name] = template
    return template
