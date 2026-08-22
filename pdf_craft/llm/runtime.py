# pylint: disable=protected-access
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from contextlib import AbstractContextManager
from typing import Self
from typing import cast

import openai
from openai.types.chat import ChatCompletionMessageParam

from .core import LLM
from .error import is_retry_error
from .increasable import Increasable
from .types import Message, MessageRole

_CACHE_LOCK = threading.Lock()


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
    """Provider runtime built from an :class:`LLM` configuration."""

    def __init__(self, config: LLM, *, protocol_version: str = "1") -> None:
        self.config = config
        self.protocol_version = protocol_version
        self._client = openai.OpenAI(api_key=config.key, base_url=config.url,
                                     timeout=config.timeout, max_retries=0)
        self._top_p, self._temperature = Increasable(config.top_p), Increasable(config.temperature)
        self._limiter = threading.BoundedSemaphore(6)
        self._logger = _create_logger(config.log_dir_path)

    def context(self, cache_seed_content: str | None = None) -> LLMContext:
        return LLMContext(self, cache_seed_content)

    def request(self, input: str | list[Message], max_tokens: int | None = None,
                temperature: float | None = None, top_p: float | None = None,
                *, cache_seed_content: str | None = None, retry_index: int | None = None,
                retry_max: int | None = None, use_cache: bool = True) -> str:
        with self.context(cache_seed_content) as context:
            return context.request(input, max_tokens, temperature, top_p,
                                   retry_index=retry_index, retry_max=retry_max,
                                   use_cache=use_cache)

    @staticmethod
    def _scheduled(value, source: Increasable, index, maximum):
        if value is not None:
            return value
        value_range = source._value_range
        if index is not None and maximum and value_range is not None:
            start, end = value_range
            return start + (end - start) * min(max(index, 0), maximum) / maximum
        return source.context().current

    def _invoke(self, messages: list[Message], max_tokens, temperature, top_p) -> str:
        converted = cast(list[ChatCompletionMessageParam], [
            {"role": message.role.name.lower(), "content": message.message} for message in messages
        ])
        with self._limiter:
            stream = self._client.chat.completions.create(model=self.config.model,
                messages=converted, stream=True, top_p=top_p, temperature=temperature,
                max_tokens=max_tokens)
            return "".join(chunk.choices[0].delta.content for chunk in stream
                           if chunk.choices and chunk.choices[0].delta.content)


class LLMContext(AbstractContextManager["LLMContext"]):
    def __init__(self, runtime: LLMRuntime, cache_seed_content: str | None) -> None:
        self.runtime, self.cache_seed_content = runtime, cache_seed_content
        self.context_id, self._pending = uuid.uuid4().hex[:12], set()
        self._top_p, self._temperature = runtime._top_p.context(), runtime._temperature.context()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for temporary in sorted(self._pending):
            if exc_type is None:
                permanent = temporary.with_name(temporary.name.rsplit(".", 2)[0] + ".txt")
                with _CACHE_LOCK:
                    if permanent.exists():
                        temporary.unlink(missing_ok=True)
                    else:
                        temporary.rename(permanent)
            else:
                temporary.unlink(missing_ok=True)

    def request(self, input, max_tokens=None, temperature=None, top_p=None, *,
                retry_index=None, retry_max=None, use_cache=True) -> str:
        messages = [Message(MessageRole.USER, input)] if isinstance(input, str) else list(input)
        temperature = self.runtime._scheduled(temperature, self.runtime._temperature, retry_index, retry_max)
        top_p = self.runtime._scheduled(top_p, self.runtime._top_p, retry_index, retry_max)
        key = self._cache_key(messages, max_tokens, temperature, top_p) if use_cache else None
        cache_path = self.runtime.config.cache_path
        if key and cache_path:
            cached = cache_path / f"{key}.txt"
            if cached.exists():
                self._log("cache-hit", 0, key=key)
                return cached.read_text(encoding="utf-8")
        self._log("cache-miss", 0, key=key)
        last_error: Exception | None = None
        empty_attempts = 0
        try:
            for attempt in range(self.runtime.config.retry_times + 1):
                try:
                    self._log("request", attempt + 1, key=key)
                    response = self.runtime._invoke(messages, max_tokens, temperature, top_p)
                    if not response.strip():
                        empty_attempts += 1
                        self._log("empty-response", attempt + 1, key=key)
                        if attempt >= self.runtime.config.retry_times:
                            raise LLMEmptyResponseError(attempts=empty_attempts)
                        continue
                    if key and cache_path:
                        temporary = cache_path / f"{key}.{self.context_id}.txt"
                        temporary.write_text(response, encoding="utf-8")
                        self._pending.add(temporary)
                    self._log("success", attempt + 1, key=key)
                    return response
                except Exception as error:
                    last_error = error
                    retryable = is_retry_error(error)
                    self._log("transport-error" if retryable else "non-retryable-error", attempt + 1, key=key, error=error)
                    if isinstance(error, LLMEmptyResponseError):
                        raise
                    if not retryable or attempt >= self.runtime.config.retry_times:
                        raise LLMTransportError("LLM transport request failed", attempts=attempt + 1, cause=error) from error
                    if self.runtime.config.retry_interval_seconds > 0:
                        time.sleep(self.runtime.config.retry_interval_seconds)
        finally:
            self._temperature.increase()
            self._top_p.increase()
        raise RuntimeError("LLM request failed") from last_error

    def _cache_key(self, messages, max_tokens, temperature, top_p) -> str:
        payload = {"url": self.runtime.config.url, "model": self.runtime.config.model,
                   "messages": [(m.role.name, m.message) for m in messages],
                   "seed": self.cache_seed_content, "temperature": temperature,
                   "top_p": top_p, "max_tokens": max_tokens,
                   "protocol": self.runtime.protocol_version}
        return hashlib.sha512(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _log(self, category: str, attempt: int, *, key: str | None, error: Exception | None = None) -> None:
        self.runtime._logger.info(json.dumps({
            "session": self.context_id, "category": category, "attempt": attempt,
            "model": self.runtime.config.model, "cache_key": key,
            **({"error": type(error).__name__} if error else {}),
        }, ensure_ascii=False))
        _close_file_handlers(self.runtime._logger)


def runtime_for(config: LLM, *, protocol_version: str = "1") -> LLMRuntime:
    return LLMRuntime(config, protocol_version=protocol_version)


def _create_logger(path):
    logger = logging.getLogger(f"pdf_craft.llm.{uuid.uuid4().hex}")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if path is not None:
        handler = logging.FileHandler(path / f"request-{uuid.uuid4().hex}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


def _close_file_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.close()
