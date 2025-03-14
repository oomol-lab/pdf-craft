from typing import Callable

# func(completed_pages: int, all_pages: int) -> None
PDFPageExtractorProgressReport = Callable[[int, int], None]