"""Execution-local policy plumbing; no component is mutated for a run."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, TypeVar, cast
from uuid import uuid4

from .concurrency import NonContinuableError, fatal_error_boundary
from .policies import (
    AllFallbackError,
    ErrorCallbackError,
    ErrorPolicy,
    ErrorPolicyInput,
    FailureCategory,
    FailureContext,
    FailureError,
    FailureLocation,
    RetryPolicy,
)

F = TypeVar("F", bound=Callable[..., Any])


class _Omitted:
    """Distinguish an inherited policy from explicit None (strict)."""


OMITTED = cast(ErrorPolicyInput, _Omitted())


@dataclass(frozen=True)
class ExecutionPolicy:
    retry: RetryPolicy
    errors: ErrorPolicyInput
    task_id: str
    operation: str
    source: str | None = None


_execution: ContextVar[ExecutionPolicy | None] = ContextVar(
    "pdf_craft_execution", default=None
)
_affected: ContextVar[tuple[FailureLocation, ...]] = ContextVar(
    "pdf_craft_affected", default=()
)
_locations: ContextVar[tuple[FailureLocation, ...]] = ContextVar(
    "pdf_craft_locations", default=()
)


def current() -> ExecutionPolicy:
    return _execution.get() or ExecutionPolicy(
        RetryPolicy(), False, uuid4().hex, "document operation"
    )


def policy_operation(function: F) -> F:
    """Scope explicit public arguments and inherit only within the current run."""
    signature = inspect.signature(function)

    @wraps(function)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs).arguments
        owner = bound.get("self")
        parent = _execution.get()
        retry = bound.get("retry_policy")
        errors = bound.get("error_policy", OMITTED)
        if parent is None:
            retry = (
                retry
                or getattr(owner, "_retry_policy", None)
                or getattr(getattr(owner, "_translator", None), "_retry_policy", None)
                or RetryPolicy()
            )
            if errors is OMITTED:
                errors = getattr(
                    owner,
                    "_error_policy",
                    getattr(
                        getattr(owner, "_translator", None), "_error_policy", False
                    ),
                )
            source = next(
                (
                    bound[key]
                    for key in ("source", "pdf_path", "source_path", "extraction")
                    if key in bound
                ),
                None,
            )
            if source is not None and not isinstance(source, (str, bytes)):
                from os import PathLike

                source = (
                    str(source)
                    if isinstance(source, PathLike)
                    else str(getattr(getattr(source, "_storage", None), "path", ""))
                    or None
                )
            policy = ExecutionPolicy(
                retry,
                errors,
                uuid4().hex,
                function.__name__,
                str(source) if source is not None else None,
            )
        else:
            policy = replace(
                parent,
                retry=retry or parent.retry,
                errors=parent.errors if errors is OMITTED else errors,
            )
        if not isinstance(policy.retry, RetryPolicy):
            raise TypeError("retry_policy must be RetryPolicy or None")
        if (
            policy.errors is not None
            and not isinstance(policy.errors, (ErrorPolicy, bool))
            and not callable(policy.errors)
        ):
            raise TypeError("error_policy must be ErrorPolicy, bool, callback or None")
        token = _execution.set(policy)
        try:
            return await function(*args, **kwargs)
        finally:
            _execution.reset(token)

    return cast(F, fatal_error_boundary(invoke))


@contextmanager
def locations(
    values: tuple[FailureLocation, ...], *, affected: tuple[FailureLocation, ...] = ()
) -> Iterator[None]:
    token = _locations.set(values)
    impact_token = _affected.set(affected)
    try:
        yield
    finally:
        _locations.reset(token)
        _affected.reset(impact_token)


def error_details(error: Exception) -> FailureError:
    chain: list[str] = []
    seen: set[int] = set()
    cause: BaseException | None = error
    request_id = code = None
    attempts = None
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if cause is not error:
            chain.append(
                f"{type(cause).__module__}.{type(cause).__qualname__}: {cause}"
            )
        attempts = attempts or getattr(cause, "attempts", None)
        request_id = request_id or getattr(cause, "request_id", None)
        code = (
            code
            or getattr(cause, "code", None)
            or getattr(cause, "status_code", None)
            or getattr(getattr(cause, "response", None), "status_code", None)
        )
        cause = cause.__cause__
    return FailureError(
        f"{type(error).__module__}.{type(error).__qualname__}",
        str(error),
        str(code) if code is not None else None,
        request_id,
        attempts,
        tuple(chain),
    )


def failure(
    error: Exception,
    category: FailureCategory,
    stage: str,
    fallback: str,
    *,
    targets: tuple[FailureLocation, ...] | None = None,
    affected: tuple[FailureLocation, ...] | None = None,
    **details,
) -> FailureContext:
    policy = current()
    values = targets if targets is not None else _locations.get()
    values = values or (FailureLocation("document", source=policy.source),)
    return FailureContext(
        policy.task_id,
        policy.operation,
        category,
        stage,
        error_details(error),
        values,
        affected if affected is not None else (_affected.get() or values),
        fallback,
        **details,
    )


def _decision(policy: ErrorPolicyInput, context: FailureContext):
    return (
        policy.decision(context.category)
        if isinstance(policy, ErrorPolicy)
        else (policy if policy is not None else False)
    )


async def decide_context(
    context: FailureContext, policy: ErrorPolicyInput = OMITTED
) -> bool:
    selected = current().errors if policy is OMITTED else policy
    decision = _decision(selected, context)
    if callable(decision):
        try:
            result = decision(context)
            if inspect.isawaitable(result):
                result = await result
        except Exception as error:
            raise ErrorCallbackError("error policy callback failed") from error
        if not isinstance(result, bool):
            raise ErrorCallbackError("error policy callback must return bool")
        return result
    return bool(decision)


async def recover(
    error: Exception, category: FailureCategory, stage: str, fallback: str, **details
) -> None:
    cause: BaseException | None = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if getattr(cause, "_pdf_craft_decided", False) or isinstance(
            cause, (NonContinuableError, AllFallbackError, ErrorCallbackError)
        ):
            raise cause
        cause = cause.__cause__
    if not await decide_context(failure(error, category, stage, fallback, **details)):
        setattr(error, "_pdf_craft_decided", True)
        raise error


def decide_sync(
    policy: ErrorPolicyInput,
    error: Exception,
    category: FailureCategory,
    stage: str,
    fallback: str,
    **details,
) -> bool:
    """For worker proxies; real asynchronous callbacks stay on the caller loop."""
    cause: BaseException | None = error
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if getattr(cause, "_pdf_craft_decided", False) or isinstance(
            cause, (NonContinuableError, AllFallbackError, ErrorCallbackError)
        ):
            raise cause
        cause = cause.__cause__
    context = failure(error, category, stage, fallback, **details)
    selected = current().errors if policy is OMITTED else policy
    decision = _decision(selected, context)
    if callable(decision):
        result = decision(context)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise RuntimeError(
                "async error callbacks require the async execution entry point"
            )
        if not isinstance(result, bool):
            raise ErrorCallbackError("error policy callback must return bool")
        if not result:
            setattr(error, "_pdf_craft_decided", True)
        return result
    if not decision:
        setattr(error, "_pdf_craft_decided", True)
    return bool(decision)


@dataclass
class Completion:
    """Counts accepted primary units, never text changes or produced wrappers."""

    succeeded: int = 0
    degraded: int = 0

    def check(self, operation: str) -> None:
        if self.degraded and not self.succeeded:
            raise AllFallbackError(operation, self.degraded)


def decision_bridge(loop, policy: ErrorPolicyInput = OMITTED):
    """Marshal native-thread failures onto the caller loop, preserving payloads."""
    import asyncio

    execution = current()
    selected = execution.errors if policy is OMITTED else policy

    def dispatch(context: FailureContext) -> bool:
        context = replace(
            context, task_id=execution.task_id, operation=execution.operation
        )
        future = asyncio.run_coroutine_threadsafe(
            decide_context(context, selected), loop
        )
        return future.result()

    return dispatch


def has_error_callback() -> bool:
    policy = current().errors
    if isinstance(policy, ErrorPolicy):
        return any(callable(policy.decision(category)) for category in FailureCategory)
    return callable(policy)


def source_locations() -> tuple[FailureLocation, ...]:
    return _locations.get()
