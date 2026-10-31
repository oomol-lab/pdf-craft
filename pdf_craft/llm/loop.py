from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar
from pathlib import Path

from .failure_log import FailureLog

from .types import Message, MessageRole
from ..usage import UsageScope

T = TypeVar("T")
S = TypeVar("S")


@dataclass(frozen=True)
class ProtocolSuccess(Generic[T, S]):
    value: T
    state: S


@dataclass(frozen=True)
class ProtocolRetry(Generic[S]):
    feedback: str
    state: S
    include_response: bool = True
    reset_history: bool = True


@dataclass(frozen=True)
class ProtocolPartial(Generic[T, S]):
    value: T
    state: S
    warning: str | None = None


@dataclass(frozen=True)
class ProtocolFailure(Generic[S]):
    error: Exception
    state: S


ProtocolResult = ProtocolSuccess[T, S] | ProtocolRetry[S] | ProtocolPartial[T, S] | ProtocolFailure[S]


class AsyncResponseProtocol(Protocol[T, S]):
    async def validate(
        self, response: str, state: S, attempt: int, max_attempts: int,
    ) -> ProtocolResult[T, S]: ...

    async def empty(
        self, state: S, attempt: int, max_attempts: int,
    ) -> ProtocolResult[T, S]: ...

    async def exhausted(
        self, state: S, attempts: int, response: str | None,
    ) -> T: ...


@dataclass
class RepairLoopOptions(Generic[T, S]):
    messages: Sequence[Message]
    request: Callable[[list[Message], int, int], Awaitable[str]]
    protocol: AsyncResponseProtocol[T, S]
    state: S
    max_attempts: int = 1
    history_limit: int = 2
    failure_log_path: Path | None = None
    failure_log_context: dict[str, Any] | None = None


async def run_repair_loop(options: RepairLoopOptions[T, S]) -> T:
    log = FailureLog(options.failure_log_path, {
        'max_attempts': max(1, options.max_attempts), 'history_limit': options.history_limit,
        **(options.failure_log_context or {}),
    })
    with log.scope():
        try:
            return await _run(options, log)
        finally:
            await log.close()


async def _run(options: RepairLoopOptions[T, S], log: FailureLog) -> T:
    initial = list(options.messages)
    current = list(initial)
    retry_history: list[Message] = []
    state = options.state
    last_response: str | None = None
    attempts = max(1, options.max_attempts)
    for attempt in range(attempts):
        log.begin(attempt + 1, current)
        with UsageScope() as scope:
            response = await options.request(current, attempt, attempts - 1)
            last_response = response
            result = (
                await options.protocol.empty(state, attempt, attempts)
                if not response.strip()
                else await options.protocol.validate(response, state, attempt, attempts)
            )
            await log.validated(response, result)
            state = result.state
            if isinstance(result, ProtocolSuccess):
                scope.accept()
                return result.value
            if isinstance(result, ProtocolPartial):
                scope.accept()
                return result.value
        if isinstance(result, ProtocolFailure):
            raise result.error
        if attempt + 1 >= attempts:
            break
        if result.reset_history:
            retry_history = []
        additions = (
            [Message(MessageRole.ASSISTANT, response)]
            if result.include_response and response else []
        )
        additions.append(Message(MessageRole.USER, result.feedback))
        retry_history = [*retry_history, *additions][-max(1, options.history_limit):]
        current = [*initial, *retry_history]
    try:
        return await options.protocol.exhausted(state, attempts, last_response)
    except Exception as error:
        identifier = await log.retain(error, attempts)
        if identifier is not None:
            setattr(error, "failure_log_id", identifier)
        raise
