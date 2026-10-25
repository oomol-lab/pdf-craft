"""Normalized remote-operation errors shared by scheduling and adapters."""
from __future__ import annotations


class OperationError(Exception):
    """A recoverable failure of one remote operation."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.attempts: int | None = None
        if cause is not None:
            self.__cause__ = cause

    def for_operation(self, operation_id: int) -> "OperationError":
        if self.operation_id is None:
            self.operation_id = operation_id
        return self


class RateLimitedError(OperationError):
    """The provider temporarily refused an operation because of rate limits."""

    def __init__(
        self,
        message: str = "The provider rate limit was exceeded.",
        *,
        operation_id: int | None = None,
        retry_after: float | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, operation_id=operation_id, cause=cause)
        self.retry_after = retry_after


class NonContinuableError(OperationError):
    """A provider failure that permanently closes one executor channel."""
