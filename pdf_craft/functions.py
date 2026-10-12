from os import PathLike

from .ocr_config import OCRConfig, ensure_ocr_config
from .pdf import OCR
from .runtime import OCR_DOMAIN


async def predownload_models(
    models_cache_path: PathLike | None = None,
    revision: str | None = None,
    ocr: OCRConfig | None = None,
) -> None:
    """Download the model required by a local OCR configuration."""
    recognizer = await OCR_DOMAIN.run(OCR,
        ocr=ensure_ocr_config(ocr, models_cache_path, False),
        pdf_handler=None,
    )
    await OCR_DOMAIN.run(recognizer.predownload, revision)
