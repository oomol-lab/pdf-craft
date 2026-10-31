"""Public retry budgets and decisions for recoverable document failures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .document.paragraph import SourceRegion
from .document.access import PageGeometry


class RetryScope(str, Enum):
    TRANSLATION = "translation"
    ANALYSIS = "analysis"
    FILL = "fill"


@dataclass(frozen=True)
class RetryPolicy:
    """Additional business attempts after the first; transport is separate.

    Translation currently has no semantic quality repair loop. Its budget is
    reserved, rather than inventing an unreliable translation-quality test.
    """

    translation: int = 3
    analysis: int = 3
    fill: int = 3

    def __post_init__(self) -> None:
        for name in ("translation", "analysis", "fill"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def attempts(self, scope: RetryScope) -> int:
        return getattr(self, scope.value) + 1


class FailureCategory(str, Enum):
    ANALYSIS = "analysis"
    TRANSLATION = "translation"
    FILL = "fill"
    OCR = "ocr"
    PDF_RENDER = "pdf_render"
    PDF_FILL = "pdf_fill"


@dataclass(frozen=True)
class FailureLocation:
    """Detached source identity; absent geometry is never fabricated.

    ``scope`` is document, page, regions, fragment, field or template. EPUB
    member paths and unit IDs identify input content, not temporary files.
    page_indexes are one-based. Regions are in original OCR pixel space.
    """

    scope: str
    source: str | None = None
    member_path: str | None = None
    unit_id: str | None = None
    channel: str | None = None
    page_indexes: tuple[int, ...] = ()
    regions: tuple[SourceRegion, ...] = ()
    epubcfi: str | None = None
    media_type: str | None = None
    field_path: str | None = None
    original: str | None = None
    page: PageGeometry | None = None
    bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class FailureError:
    """Serializable error details, even when the original exception is not."""

    type: str
    message: str
    code: str | None = None
    request_id: str | None = None
    attempts: int | None = None
    causes: tuple[str, ...] = ()
    # Serializable traceback locations; excludes source text and frame locals.
    frames: tuple[dict[str, str | int], ...] = ()
    http_status: int | None = None
    provider_code: str | None = None
    provider_type: str | None = None
    # Each entry has its own traceback; messages may contain private source text.
    exception_chain: tuple[FailureError, ...] = ()
    failure_log_id: str | None = None


@dataclass(frozen=True)
class FailureContext:
    """Final failure and the *proposed* recovery, before the user's decision.

    Targets are operands of the failed operation. Affected locations are the
    wider recovery footprint; neither claims to pinpoint a network root cause.
    """

    task_id: str
    operation: str
    category: FailureCategory
    stage: str
    error: FailureError
    targets: tuple[FailureLocation, ...]
    affected: tuple[FailureLocation, ...]
    fallback: str
    backend: str | None = None
    model: str | None = None
    stage_index: int | None = None
    context_locations: tuple[FailureLocation, ...] = ()


ErrorCallback: TypeAlias = Callable[[FailureContext], bool | Awaitable[bool]]
ErrorDecision: TypeAlias = bool | ErrorCallback


@dataclass(frozen=True)
class ErrorPolicy:
    """False raises; True recovers; a callback decides each final failure."""

    ignore_analysis_errors: ErrorDecision = False
    ignore_translation_errors: ErrorDecision = False
    ignore_fill_errors: ErrorDecision = False
    ignore_ocr_errors: ErrorDecision = False
    ignore_pdf_render_errors: ErrorDecision = False
    ignore_pdf_fill_errors: ErrorDecision = False

    def __post_init__(self) -> None:
        for category in FailureCategory:
            value = self.decision(category)
            if not isinstance(value, bool) and not callable(value):
                raise TypeError(
                    f"ignore_{category.value}_errors must be bool or callable"
                )

    def decision(self, category: FailureCategory) -> ErrorDecision:
        return getattr(self, f"ignore_{category.value}_errors")


ErrorPolicyInput: TypeAlias = ErrorPolicy | None | bool | ErrorCallback


class AllFallbackError(Exception):
    """The requested primary work produced no accepted result due to errors."""

    def __init__(
        self, operation: str, failed_units: int, *, message: str | None = None
    ):
        super().__init__(
            message
            or f"{operation}: all {failed_units} target units fell back after errors"
        )
        self.operation = operation
        self.failed_units = failed_units


class ErrorCallbackError(Exception):
    """A decision callback failed; this is never eligible for recovery."""
