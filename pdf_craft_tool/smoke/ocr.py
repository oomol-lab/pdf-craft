from typing import Any

from pdf_craft import (
    DeepSeekOCR2LocalConfig,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRLocalConfig,
    DeepSeekOCRVendorConfig,
    OCRConfig,
    OCRMode,
    UnlimitedOCRLocalConfig,
    UnlimitedOCRVendorConfig,
)


def create_ocr_config(mode: OCRMode, values: dict[str, Any]) -> OCRConfig:
    """Build an OCR config from explicit caller-supplied values only."""
    if mode == "deepseek-ocr-local":
        return DeepSeekOCRLocalConfig(**values)
    if mode == "deepseek-ocr2-local":
        return DeepSeekOCR2LocalConfig(**values)
    if mode == "unlimited-ocr-local":
        return UnlimitedOCRLocalConfig(**values)
    if mode == "deepseek-ocr-vendor":
        return DeepSeekOCRVendorConfig(**values)
    if mode == "deepseek-ocr2-vendor":
        return DeepSeekOCR2VendorConfig(**values)
    if mode == "unlimited-ocr-vendor":
        return UnlimitedOCRVendorConfig(**values)
    raise ValueError(f"unsupported OCR mode: {mode}")
