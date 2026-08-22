from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from json_repair import repair_json
from pydantic import BaseModel, ValidationError

from .types import Message, MessageRole

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


def request_guaranteed_json(options: GuaranteedOptions[TData, TResult]) -> TResult:
    initial = list(options.messages)
    current = list(initial)
    last_response: str | None = None
    for index in range(options.max_retries + 1):
        response = options.request(current, index, options.max_retries)
        last_response = response
        if not response or not response.strip():
            if index >= options.max_retries:
                raise GuaranteedEmptyResponseError("LLM returned empty response after all retries", attempts=index + 1)
            continue
        try:
            data = json.loads(repair_json(_extract_json(response)))
        except (ValueError, json.JSONDecodeError) as error:
            if _looks_like_refusal(response) and index >= min(1, options.max_retries):
                raise GuaranteedProtocolError("LLM returned natural language instead of JSON", attempts=index + 1, response=response) from error
            if index >= options.max_retries:
                raise GuaranteedExhaustedError("JSON syntax remained invalid after retries", attempts=index + 1, response=response, cause=error) from error
            current = _retry_messages(initial, response, "Return complete valid JSON only; do not explain or use markdown fences.", include_response=True)
            continue
        try:
            validated = options.schema.model_validate(data)
        except ValidationError as error:
            if index >= options.max_retries:
                raise GuaranteedSchemaError("JSON schema validation failed after retries", attempts=index + 1, response=response, cause=error) from error
            current = _retry_messages(initial, response, _schema_feedback(error), include_response=True)
            continue
        try:
            return options.parse(cast(TData, validated), index, options.max_retries)
        except Exception as error:
            if index >= options.max_retries:
                raise GuaranteedBusinessError("JSON business validation failed after retries", attempts=index + 1, response=response, cause=error) from error
            current = _retry_messages(initial, response, f"Fix the business validation error and return complete JSON:\n{error}", include_response=True)
    raise GuaranteedExhaustedError("Guaranteed JSON request failed", attempts=options.max_retries + 1, response=last_response)


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
