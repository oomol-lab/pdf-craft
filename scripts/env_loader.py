import os
from pathlib import Path
from typing import cast

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


def load_env(path: Path) -> None:
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def create_ocr_config_from_env() -> OCRConfig:
    mode = cast(OCRMode, _env_str("OCR_MODE", default="deepseek-ocr-local"))
    if mode == "deepseek-ocr-local":
        return DeepSeekOCRLocalConfig(
            models_cache_path=_env_str(
                "DEEPSEEK_LOCAL_MODEL_PATH",
                default="models-cache",
            ),
            local_only=_env_bool("DEEPSEEK_LOCAL_ONLY", default=True),
        )
    if mode == "deepseek-ocr2-local":
        return DeepSeekOCR2LocalConfig(
            models_cache_path=_env_str(
                "DEEPSEEK_LOCAL_MODEL_PATH",
                default="models-cache",
            ),
            local_only=_env_bool("DEEPSEEK_LOCAL_ONLY", default=True),
        )
    if mode == "unlimited-ocr-local":
        return UnlimitedOCRLocalConfig(
            models_cache_path=_env_str(
                "UNLIMITED_LOCAL_MODEL_PATH",
                default="models-cache",
            ),
            local_only=_env_bool("UNLIMITED_LOCAL_ONLY", default=True),
        )
    if mode == "deepseek-ocr-vendor":
        return DeepSeekOCRVendorConfig(
            base_url=_required_env("DEEPSEEK_OCR_BASE_URL"),
            api_key=_required_env("DEEPSEEK_OCR_API_KEY"),
            model=_required_env("DEEPSEEK_OCR_MODEL"),
            temperature=_env_optional_float("DEEPSEEK_OCR_TEMPERATURE"),
            top_p=_env_optional_float("DEEPSEEK_OCR_TOP_P"),
            max_tokens=_env_int("DEEPSEEK_OCR_MAX_TOKENS", default=8000),
            timeout_seconds=_env_int("DEEPSEEK_OCR_TIMEOUT_SECONDS", default=180),
        )
    if mode == "deepseek-ocr2-vendor":
        return DeepSeekOCR2VendorConfig(
            base_url=_required_env("DEEPSEEK_OCR2_BASE_URL"),
            api_key=_required_env("DEEPSEEK_OCR2_API_KEY"),
            model=_required_env("DEEPSEEK_OCR2_MODEL"),
            temperature=_env_optional_float("DEEPSEEK_OCR2_TEMPERATURE"),
            top_p=_env_optional_float("DEEPSEEK_OCR2_TOP_P"),
            max_tokens=_env_int("DEEPSEEK_OCR2_MAX_TOKENS", default=8000),
            timeout_seconds=_env_int("DEEPSEEK_OCR2_TIMEOUT_SECONDS", default=180),
        )
    if mode == "unlimited-ocr-vendor":
        return UnlimitedOCRVendorConfig(
            ak=_required_env("UNLIMITED_OCR_ACCESS_KEY"),
            sk=_required_env("UNLIMITED_OCR_SECRET_KEY"),
            base_url=_env_str(
                "UNLIMITED_OCR_BASE_URL",
                default="https://aip.baidubce.com",
            ),
            poll_interval_seconds=_env_float(
                "UNLIMITED_OCR_POLL_INTERVAL_SECONDS",
                default=2.0,
            ),
            timeout_seconds=_env_int("UNLIMITED_OCR_TIMEOUT_SECONDS", default=180),
        )
    raise SystemExit(
        "OCR_MODE must be one of: "
        "deepseek-ocr-local, deepseek-ocr2-local, unlimited-ocr-local, "
        "deepseek-ocr-vendor, deepseek-ocr2-vendor, unlimited-ocr-vendor"
    )


def _env_str(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if default is not None:
        return default
    return ""


def _required_env(name: str) -> str:
    value = _env_str(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env_str(name)
    if not value:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean, got {value!r}")


def _env_int(name: str, default: int = 0) -> int:
    value = _env_str(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {value!r}") from exc


def _env_float(name: str, default: float = 0.0) -> float:
    value = _env_str(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a float, got {value!r}") from exc


def _env_optional_float(name: str) -> float | None:
    value = _env_str(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a float, got {value!r}") from exc
