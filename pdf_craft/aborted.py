from typing import Callable
from doc_page_extractor import AbortError

AbortedCheck = Callable[[], bool]

def check_aborted(aborted_check: AbortedCheck) -> None:
    if aborted_check():
        raise AbortError()