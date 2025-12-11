from .metering import OCRTokensMetering, InterruptedKind


class PDFError(Exception):
    def __init__(self, message: str, page_index: int | None = None) -> None:
        super().__init__(message)
        self.page_index: int | None = page_index


class OCRError(Exception):
    def __init__(self, message: str, page_index: int, step_index: int) -> None:
        super().__init__(message)
        self.page_index: int = page_index
        self.step_index: int = step_index

def is_inline_error(error: Exception) -> bool:
    return isinstance(error, (PDFError, OCRError))


# 不可直接用 doc-page-extractor 的 Error，该库的一切都是懒加载，若暴露，则无法懒加载
class InterruptedError(Exception):
    """Raised when the operation is interrupted by the user."""
    def __init__(self, metering: OCRTokensMetering) -> None:
        super().__init__()
        self._kind: InterruptedKind
        self._metering: OCRTokensMetering = metering

def to_interrupted_error(error: Exception) -> InterruptedError | None:
    from doc_page_extractor import AbortError, TokenLimitError, ExtractionAbortedError
    if isinstance(error, ExtractionAbortedError):
        kind: InterruptedKind | None = None
        if isinstance(error, AbortError):
            kind = InterruptedKind.ABORT
        elif isinstance(error, TokenLimitError):
            kind = InterruptedKind.TOKEN_LIMIT_EXCEEDED
        if kind is not None:
            return InterruptedError(OCRTokensMetering(
                input_tokens=error.input_tokens,
                output_tokens=error.output_tokens,
            ))
    return None
