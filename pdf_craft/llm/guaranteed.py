# pylint: disable=unused-argument
from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from .types import Message, MessageRole
from .loop import (ProtocolFailure, ProtocolRetry, ProtocolSuccess, RepairLoopOptions,
                   run_repair_loop)

TData = TypeVar("TData")
TResult = TypeVar("TResult")


class GuaranteedRequestError(Exception):
    def __init__(self, message: str, *, attempts: int, response: str | None = None, cause: Exception | None = None):
        super().__init__(message)
        self.attempts, self.response, self.__cause__ = attempts, response, cause


class GuaranteedEmptyResponseError(GuaranteedRequestError):
    pass


class GuaranteedProtocolError(GuaranteedRequestError):
    pass


class GuaranteedSchemaError(GuaranteedRequestError):
    pass


class GuaranteedBusinessError(GuaranteedRequestError):
    pass


class GuaranteedExhaustedError(GuaranteedRequestError):
    pass


@dataclass(frozen=True)
class GuaranteedOptions(Generic[TData, TResult]):
    messages: Sequence[Message]
    request: Callable[[list[Message], int, int], str]
    schema: type[BaseModel]
    parse: Callable[[TData, int, int], TResult]
    max_retries: int = 12
    extractor: Callable[[str], str] | None = None


def request_guaranteed_json(options: GuaranteedOptions[TData, TResult]) -> TResult:
    class _JsonProtocol:
        def __init__(self) -> None:
            self.last_error: GuaranteedRequestError | None = None

        def validate(self, response: str, state: None, attempt: int, max_attempts: int):
            try:
                extractor = options.extractor or _extract_json
                data = json.loads(repair_json(extractor(response)))
            except (ValueError, json.JSONDecodeError) as error:
                if _looks_like_refusal(response) and attempt >= min(1, options.max_retries):
                    return ProtocolFailure(GuaranteedProtocolError("LLM returned natural language instead of JSON", attempts=attempt + 1, response=response, cause=error), state)
                self.last_error = GuaranteedExhaustedError("JSON syntax remained invalid after retries", attempts=attempt + 1, response=response, cause=error)
                return ProtocolRetry("Return complete valid JSON only; do not explain or use markdown fences.", state)
            try:
                validated = options.schema.model_validate(data)
            except ValidationError as error:
                self.last_error = GuaranteedSchemaError("JSON schema validation failed after retries", attempts=attempt + 1, response=response, cause=error)
                return ProtocolRetry(_schema_feedback(error), state)
            try:
                return ProtocolSuccess(options.parse(cast(TData, validated), attempt, max_attempts), state)
            except Exception as error:
                self.last_error = GuaranteedBusinessError("JSON business validation failed after retries", attempts=attempt + 1, response=response, cause=error)
                return ProtocolRetry(f"Fix the business validation error and return complete JSON:\n{error}", state)

        def empty(self, state: None, attempt: int, max_attempts: int):
            return ProtocolRetry("Return a non-empty complete JSON response.", state)

        def exhausted(self, state: None, attempts: int, response: str | None) -> TResult:
            if self.last_error is not None:
                raise self.last_error
            raise GuaranteedEmptyResponseError("LLM returned empty response after all retries", attempts=attempts, response=response)

    return run_repair_loop(RepairLoopOptions(messages=options.messages, request=options.request,
        protocol=_JsonProtocol(), state=None, max_attempts=options.max_retries + 1))


def _extract_json(response: str) -> str:
    text = re.sub(r"```(?:json)?\s*\n?(.*?)\n?```", r"\1", response, flags=re.I | re.S).strip()
    candidates = [m for m in (re.search(r"\[[\s\S]*\]", text), re.search(r"\{[\s\S]*\}", text)) if m]
    return min(candidates, key=lambda m: m.start()).group(0) if candidates else text


def _looks_like_refusal(text: str) -> bool:
    return bool(re.search(r"\b(sorry|cannot|can't|unable to|do not understand)\b|抱歉|无法", text, re.I)) and not re.search(r"[\[{]", text)


def _retry_messages(initial, response, feedback, *, include_response):
    return [*initial, *([Message(MessageRole.ASSISTANT, response)] if include_response else []), Message(MessageRole.USER, feedback)]


def _schema_feedback(error: ValidationError) -> str:
    return "Your JSON has structural issues:\n" + "\n".join(f"- {'.'.join(map(str, issue['loc']))}: {issue['msg']}" for issue in error.errors()) + "\nReturn complete JSON only."
