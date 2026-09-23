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
from typing import Self, cast

import httpx
import openai
from openai.types.chat import ChatCompletionMessageParam

from ..runtime import IO_DOMAIN, run_sync
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

    def __init__(self, config: LLM, *, protocol_version: str = "1") -> None:
        self.config = config
        self.protocol_version = protocol_version
        self._top_p, self._temperature = Increasable(config.top_p), Increasable(config.temperature)
        self._limiter = asyncio.Semaphore(6)
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

    async def _invoke_async(self, messages: list[Message], max_tokens, temperature, top_p) -> str:
        converted = cast(list[ChatCompletionMessageParam], [
            {"role": message.role.name.lower(), "content": message.message} for message in messages
        ])
        async with self._limiter:
            try:
                return await self._invoke_stream(
                    converted, max_tokens, temperature, top_p, force_ipv4=False,
                )
            except (openai.APIConnectionError, httpx.ConnectError) as error:
                if not _caused_by_connect_error(error):
                    raise
                return await self._invoke_stream(
                    converted, max_tokens, temperature, top_p, force_ipv4=True,
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

    async def _invoke_for_request(self, messages, max_tokens, temperature, top_p) -> str:
        # Preserve the established test/custom transport seam. Production has
        # no synchronous _invoke member and always takes _invoke_async above.
        override = self.__dict__.get("_invoke")
        if override is None:
            return await self._invoke_async(messages, max_tokens, temperature, top_p)
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
            for attempt in range(self.runtime.config.retry_times + 1):
                try:
                    await self._log_async("request", attempt + 1, key=key)
                    response = await self.runtime._invoke_for_request(
                        messages, max_tokens, temperature, top_p,
                    )
                    if not response.strip():
                        empty_attempts += 1
                        await self._log_async("empty-response", attempt + 1, key=key)
                        if attempt >= self.runtime.config.retry_times:
                            raise LLMEmptyResponseError(attempts=empty_attempts)
                        continue
                    if key and cache_path:
                        temporary = cache_path / f"{key}.{self.context_id}.txt"
                        await IO_DOMAIN.run(_write_cached, temporary, response)
                        self._pending.add(temporary)
                    await self._log_async("success", attempt + 1, key=key)
                    return response
                except Exception as error:
                    last_error = error
                    retryable = is_retry_error(error)
                    await self._log_async(
                        "transport-error" if retryable else "non-retryable-error",
                        attempt + 1, key=key, error=error,
                    )
                    if isinstance(error, LLMEmptyResponseError):
                        raise
                    if not retryable or attempt >= self.runtime.config.retry_times:
                        raise LLMTransportError(
                            "LLM transport request failed", attempts=attempt + 1, cause=error,
                        ) from error
                    if self.runtime.config.retry_interval_seconds > 0:
                        await asyncio.sleep(self.runtime.config.retry_interval_seconds)
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


def runtime_for(config: LLM, *, protocol_version: str = "1") -> LLMRuntime:
    return LLMRuntime(config, protocol_version=protocol_version)


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
