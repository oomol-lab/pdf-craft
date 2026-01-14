from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

AbortedCheck = Callable[[], bool]


def check_aborted(aborted_check: AbortedCheck) -> None:
    if aborted_check():
        from doc_page_extractor import AbortError

        raise AbortError()


@dataclass
class OCRTokensMetering:
    input_tokens: int
    output_tokens: int


class InterruptedKind(Enum):
    ABORT = auto()
    TOKEN_LIMIT_EXCEEDED = auto()
