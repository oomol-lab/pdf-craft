from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from .types import Message, MessageRole

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


class ResponseProtocol(Protocol[T, S]):
    def validate(self, response: str, state: S, attempt: int, max_attempts: int) -> ProtocolResult[T, S]: ...

    def empty(self, state: S, attempt: int, max_attempts: int) -> ProtocolResult[T, S]: ...

    def exhausted(self, state: S, attempts: int, response: str | None) -> T: ...


@dataclass
class RepairLoopOptions(Generic[T, S]):
    messages: Sequence[Message]
    request: Callable[[list[Message], int, int], str]
    protocol: ResponseProtocol[T, S]
    state: S
    max_attempts: int = 1
    history_limit: int = 2


def run_repair_loop(options: RepairLoopOptions[T, S]) -> T:
    initial = list(options.messages)
    current = list(initial)
    state = options.state
    last_response: str | None = None
    attempts = max(1, options.max_attempts)
    for attempt in range(attempts):
        response = options.request(current, attempt, attempts - 1)
        last_response = response
        result = options.protocol.empty(state, attempt, attempts) if not response.strip() else options.protocol.validate(response, state, attempt, attempts)
        state = result.state
        if isinstance(result, ProtocolSuccess):
            return result.value
        if isinstance(result, ProtocolPartial):
            return result.value
        if isinstance(result, ProtocolFailure):
            raise result.error
        if attempt + 1 >= attempts:
            break
        base = initial if result.reset_history else current
        additions = ([Message(MessageRole.ASSISTANT, response)] if result.include_response and response else [])
        additions.append(Message(MessageRole.USER, result.feedback))
        current = [*base, *additions][-max(1, options.history_limit + len(initial)):]
    return options.protocol.exhausted(state, attempts, last_response)
