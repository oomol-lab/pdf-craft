"""Private runtime configuration for repository manual scripts.

This module is deliberately outside :mod:`pdf_craft`: library callers pass
configuration objects explicitly and never load a repository ``.env``.
"""

import os
from pathlib import Path
from typing import cast

from pdf_craft import (
    DeepSeekOCR2LocalConfig,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRLocalConfig,
    DeepSeekOCRVendorConfig,
    LLM,
    OCRConfig,
    OCRMode,
    UnlimitedOCRLocalConfig,
    UnlimitedOCRVendorConfig,
)


def load_project_env(project_root: Path) -> Path:
    """Load the worktree-private ``.env`` used by manual scripts."""
    path = project_root / ".env"
    if not path.is_file():
        raise SystemExit(f"Missing {path}; copy .env.template and configure it first")
    load_env(path)
    return path


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def create_ocr_config_from_env() -> OCRConfig:
    """Create an explicit pdf-craft OCR config from ``PDF_CRAFT_*`` settings."""
    mode = cast(OCRMode, _str("PDF_CRAFT_OCR_MODE", default="deepseek-ocr-local"))
    if mode == "deepseek-ocr-local":
        return DeepSeekOCRLocalConfig(
            models_cache_path=_str("PDF_CRAFT_DEEPSEEK_MODELS_CACHE_PATH", default="models-cache"),
            local_only=_bool("PDF_CRAFT_DEEPSEEK_LOCAL_ONLY", default=True),
        )
    if mode == "deepseek-ocr2-local":
        return DeepSeekOCR2LocalConfig(
            models_cache_path=_str("PDF_CRAFT_DEEPSEEK_MODELS_CACHE_PATH", default="models-cache"),
            local_only=_bool("PDF_CRAFT_DEEPSEEK_LOCAL_ONLY", default=True),
        )
    if mode == "unlimited-ocr-local":
        return UnlimitedOCRLocalConfig(
            models_cache_path=_str("PDF_CRAFT_UNLIMITED_MODELS_CACHE_PATH", default="models-cache"),
            local_only=_bool("PDF_CRAFT_UNLIMITED_LOCAL_ONLY", default=True),
        )
    if mode == "deepseek-ocr-vendor":
        return DeepSeekOCRVendorConfig(
            base_url=_required("PDF_CRAFT_DEEPSEEK_OCR_BASE_URL"),
            api_key=_required("PDF_CRAFT_DEEPSEEK_OCR_API_KEY"),
            model=_required("PDF_CRAFT_DEEPSEEK_OCR_MODEL"),
            temperature=_optional_float("PDF_CRAFT_DEEPSEEK_OCR_TEMPERATURE"),
            top_p=_optional_float("PDF_CRAFT_DEEPSEEK_OCR_TOP_P"),
            max_tokens=_int("PDF_CRAFT_DEEPSEEK_OCR_MAX_TOKENS", default=8000),
            timeout_seconds=_int("PDF_CRAFT_DEEPSEEK_OCR_TIMEOUT_SECONDS", default=180),
        )
    if mode == "deepseek-ocr2-vendor":
        return DeepSeekOCR2VendorConfig(
            base_url=_required("PDF_CRAFT_DEEPSEEK_OCR2_BASE_URL"),
            api_key=_required("PDF_CRAFT_DEEPSEEK_OCR2_API_KEY"),
            model=_required("PDF_CRAFT_DEEPSEEK_OCR2_MODEL"),
            temperature=_optional_float("PDF_CRAFT_DEEPSEEK_OCR2_TEMPERATURE"),
            top_p=_optional_float("PDF_CRAFT_DEEPSEEK_OCR2_TOP_P"),
            max_tokens=_int("PDF_CRAFT_DEEPSEEK_OCR2_MAX_TOKENS", default=8000),
            timeout_seconds=_int("PDF_CRAFT_DEEPSEEK_OCR2_TIMEOUT_SECONDS", default=180),
        )
    if mode == "unlimited-ocr-vendor":
        return UnlimitedOCRVendorConfig(
            ak=_required("PDF_CRAFT_UNLIMITED_OCR_ACCESS_KEY"),
            sk=_required("PDF_CRAFT_UNLIMITED_OCR_SECRET_KEY"),
            base_url=_str("PDF_CRAFT_UNLIMITED_OCR_BASE_URL", default="https://aip.baidubce.com"),
            poll_interval_seconds=_float("PDF_CRAFT_UNLIMITED_OCR_POLL_INTERVAL_SECONDS", default=2.0),
            timeout_seconds=_int("PDF_CRAFT_UNLIMITED_OCR_TIMEOUT_SECONDS", default=180),
        )
    raise SystemExit(f"Unsupported PDF_CRAFT_OCR_MODE: {mode}")


def create_translation_llm_from_env(*, cache_path: Path, log_dir_path: Path) -> LLM:
    """Create the LLM used for text translation, distinct from OCR vendors."""
    return LLM(
        key=_required("PDF_CRAFT_TRANSLATION_API_KEY"),
        url=_required("PDF_CRAFT_TRANSLATION_BASE_URL"),
        model=_required("PDF_CRAFT_TRANSLATION_MODEL"),
        token_encoding=_str("PDF_CRAFT_TRANSLATION_TOKEN_ENCODING", default="o200k_base"),
        timeout=_float("PDF_CRAFT_TRANSLATION_TIMEOUT_SECONDS", default=180.0),
        temperature=_optional_float("PDF_CRAFT_TRANSLATION_TEMPERATURE"),
        top_p=_optional_float("PDF_CRAFT_TRANSLATION_TOP_P"),
        retry_times=_int("PDF_CRAFT_TRANSLATION_RETRY_TIMES", default=3),
        cache_path=cache_path,
        log_dir_path=log_dir_path,
    )


def _str(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, "").strip()
    return value or (default or "")


def _required(name: str) -> str:
    value = _str(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    value = _str(name)
    if not value:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean")


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError as error:
        raise SystemExit(f"{name} must be an integer") from error


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError as error:
        raise SystemExit(f"{name} must be a number") from error


def _optional_float(name: str) -> float | None:
    value = _str(name)
    return float(value) if value else None
