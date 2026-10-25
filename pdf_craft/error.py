from .concurrency import NonContinuableError
from .policies import AllFallbackError
from .metering import InterruptedKind, OCRTokensMetering


class PDFError(Exception):
    def __init__(self, message: str, page_index: int | None = None) -> None:
        super().__init__(message)
        self.page_index: int | None = page_index


class PDFPreflightError(NonContinuableError):
    """Legacy input-gate error retained for import and serialization compatibility.

    Conversion and optional input assertions no longer raise this error.
    """

    def __init__(self, source: str, backend: str, stage: str,
                 page_index: int | None, reason: str) -> None:
        self.source = source
        self.backend = backend
        self.stage = stage
        self.page_index = page_index
        self.reason = reason
        location = f" on page {page_index}" if page_index is not None else ""
        super().__init__(f"PDF preflight failed ({backend}/{stage}){location}: {reason}")

    def __reduce__(self):
        return type(self), (self.source, self.backend, self.stage, self.page_index, self.reason), {
            **self.__dict__, "__cause__": self.__cause__,
        }

    def __setstate__(self, state):
        self.__cause__ = state.pop("__cause__", None)
        self.__dict__.update(state)


class OCRError(Exception):
    def __init__(self, message: str, page_index: int, step_index: int) -> None:
        super().__init__(message)
        self.page_index: int = page_index
        self.step_index: int = step_index


class OCRFatalError(NonContinuableError):
    """An OCR service failure which must stop conversion immediately."""


class OCRBillingError(OCRFatalError):
    """Raised when the OCR provider requires payment before serving a page."""

    def __init__(self, page_index: int) -> None:
        super().__init__(
            f"OCR service requires payment before page {page_index} can be processed."
        )
        self.page_index = page_index


class NoUsableOCRPagesError(AllFallbackError):
    """Raised when every requested OCR page used error recovery."""

    def __init__(self, failed_page_indexes: tuple[int, ...]) -> None:
        self.failed_page_indexes = failed_page_indexes
        pages = ", ".join(str(index) for index in failed_page_indexes)
        super().__init__(
            "extract_pdf",
            len(failed_page_indexes),
            message="PDF extraction produced no usable pages; all requested pages fell back after "
            f"errors were ignored: {pages}",
        )

    def __reduce__(self):
        return type(self), (self.failed_page_indexes,)


def is_inline_error(error: Exception) -> bool:
    return isinstance(error, (PDFError, OCRError))


class NoUsableFillPagesError(AllFallbackError):
    """Raised when all pages scheduled for fill used error recovery."""

    def __init__(self, failed_page_indexes: tuple[int, ...]) -> None:
        self.failed_page_indexes = failed_page_indexes
        pages = ", ".join(str(index) for index in failed_page_indexes)
        super().__init__(
            "patch_pdf",
            len(failed_page_indexes),
            message="PDF fill produced no usable pages; all pages scheduled for fill "
            f"failed after errors were ignored: {pages}",
        )

    def __reduce__(self):
        return type(self), (self.failed_page_indexes,)


# 不可直接用 doc-page-extractor 的 Error，该库的一切都是懒加载，若暴露，则无法懒加载
class InterruptedError(Exception):
    """Raised when the operation is interrupted by the user."""

    def __init__(self, kind: InterruptedKind, metering: OCRTokensMetering) -> None:
        super().__init__()
        self._kind: InterruptedKind = kind
        self._metering: OCRTokensMetering = metering

    @property
    def kind(self) -> InterruptedKind:
        return self._kind

    @property
    def metering(self) -> OCRTokensMetering:
        return self._metering


def to_interrupted_error(error: Exception) -> InterruptedError | None:
    from doc_page_extractor.extraction_context import (
        AbortError,
        ExtractionAbortedError,
        TokenLimitError,
    )

    if isinstance(error, ExtractionAbortedError):
        kind: InterruptedKind | None = None
        if isinstance(error, AbortError):
            kind = InterruptedKind.ABORT
        elif isinstance(error, TokenLimitError):
            kind = InterruptedKind.TOKEN_LIMIT_EXCEEDED

        if kind is not None:
            return InterruptedError(
                kind=kind,
                metering=OCRTokensMetering(
                    input_tokens=error.input_tokens,
                    output_tokens=error.output_tokens,
                ),
            )
    return None
