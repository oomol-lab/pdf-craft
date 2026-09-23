from os import PathLike

from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import OCR, PDFHandler
from .runtime import require_sync_context


def predownload_models(
    models_cache_path: PathLike | None = None,
    pdf_handler: PDFHandler | None = None,
    revision: str | None = None,
    ocr: OCRConfig | None = None,
) -> None:
    """Download the model required by a local OCR configuration."""
    require_sync_context()
    recognizer = OCR(
        ocr=ensure_ocr_config(ocr, models_cache_path, False),
        pdf_handler=pdf_handler,
    )
    recognizer.predownload(revision)
