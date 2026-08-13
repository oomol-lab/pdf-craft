from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Iterable, Literal, TypeAlias

from .to_path import to_path


@dataclass(frozen=True)
class LocalDeepSeekOCRConfig:
    models_cache_path: Path | None = None
    local_only: bool = False
    enable_devices_numbers: Iterable[int] | None = None

    def __init__(
        self,
        models_cache_path: PathLike | str | None = None,
        local_only: bool = False,
        enable_devices_numbers: Iterable[int] | None = None,
    ) -> None:
        object.__setattr__(
            self,
            "models_cache_path",
            to_path(models_cache_path) if models_cache_path is not None else None,
        )
        object.__setattr__(self, "local_only", local_only)
        object.__setattr__(self, "enable_devices_numbers", enable_devices_numbers)

    @classmethod
    def from_env(cls) -> "LocalDeepSeekOCRConfig":
        import os

        models_cache_path = os.environ.get("PDF_CRAFT_MODELS_CACHE_PATH") or os.environ.get(
            "DOC_PAGE_EXTRACTOR_MODEL_PATH"
        )
        return cls(
            models_cache_path=models_cache_path.strip() if models_cache_path else None,
            local_only=_env_bool(
                "PDF_CRAFT_LOCAL_ONLY",
                fallback_name="DOC_PAGE_EXTRACTOR_LOCAL_ONLY",
                default=False,
            ),
        )


@dataclass(frozen=True)
class VendorDeepSeekOCRConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int = 8000
    timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "VendorDeepSeekOCRConfig":
        return cls(
            base_url=_required_env(
                "PDF_CRAFT_DEEPSEEK_BASE_URL",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_BASE_URL",
            ),
            api_key=_required_env(
                "PDF_CRAFT_DEEPSEEK_API_KEY",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_API_KEY",
            ),
            model=_required_env(
                "PDF_CRAFT_DEEPSEEK_MODEL",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_MODEL",
            ),
            temperature=_env_optional_float(
                "PDF_CRAFT_DEEPSEEK_TEMPERATURE",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_TEMPERATURE",
            ),
            top_p=_env_optional_float(
                "PDF_CRAFT_DEEPSEEK_TOP_P",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_TOP_P",
            ),
            max_tokens=_env_int(
                "PDF_CRAFT_DEEPSEEK_MAX_TOKENS",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_MAX_TOKENS",
                default=8000,
            ),
            timeout_seconds=_env_int(
                "PDF_CRAFT_DEEPSEEK_TIMEOUT_SECONDS",
                fallback_name="DOC_PAGE_EXTRACTOR_DEEPSEEK_VENDOR_TIMEOUT_SECONDS",
                default=180,
            ),
        )


@dataclass(frozen=True)
class VendorUnlimitedOCRConfig:
    ak: str
    sk: str
    base_url: str = "https://aip.baidubce.com"
    poll_interval_seconds: float = 2.0
    timeout_seconds: int = 180

    @classmethod
    def from_env(cls) -> "VendorUnlimitedOCRConfig":
        return cls(
            ak=_required_env(
                "PDF_CRAFT_UNLIMITED_AK",
                fallback_name="DOC_PAGE_EXTRACTOR_BAIDU_AK",
            ),
            sk=_required_env(
                "PDF_CRAFT_UNLIMITED_SK",
                fallback_name="DOC_PAGE_EXTRACTOR_BAIDU_SK",
            ),
            base_url=_env_str(
                "PDF_CRAFT_UNLIMITED_BASE_URL",
                fallback_name="DOC_PAGE_EXTRACTOR_BAIDU_BASE_URL",
                default="https://aip.baidubce.com",
            ),
            poll_interval_seconds=_env_float(
                "PDF_CRAFT_UNLIMITED_POLL_INTERVAL_SECONDS",
                fallback_name="DOC_PAGE_EXTRACTOR_BAIDU_POLL_INTERVAL_SECONDS",
                default=2.0,
            ),
            timeout_seconds=_env_int(
                "PDF_CRAFT_UNLIMITED_TIMEOUT_SECONDS",
                fallback_name="DOC_PAGE_EXTRACTOR_BAIDU_TIMEOUT_SECONDS",
                default=180,
            ),
        )


VendorOCRConfig: TypeAlias = VendorDeepSeekOCRConfig | VendorUnlimitedOCRConfig
OCRConfig: TypeAlias = LocalDeepSeekOCRConfig | VendorOCRConfig
OCRMode: TypeAlias = Literal["local-deepseek", "vendor-deepseek", "vendor-unlimited"]


def create_ocr_config_from_env() -> OCRConfig:
    mode = _env_str("PDF_CRAFT_OCR_MODE")
    if not mode:
        legacy_mode = _env_str("DOC_PAGE_EXTRACTOR_BACKEND")
        if legacy_mode == "vendor":
            mode = "vendor-deepseek"
        elif legacy_mode == "baidu":
            mode = "vendor-unlimited"
        elif legacy_mode in {"local", "cuda"}:
            mode = "local-deepseek"
        else:
            mode = "local-deepseek"
    if mode == "local-deepseek":
        return LocalDeepSeekOCRConfig.from_env()
    if mode == "vendor-deepseek":
        return VendorDeepSeekOCRConfig.from_env()
    if mode == "vendor-unlimited":
        return VendorUnlimitedOCRConfig.from_env()
    raise SystemExit(
        "PDF_CRAFT_OCR_MODE must be one of: "
        "local-deepseek, vendor-deepseek, vendor-unlimited"
    )


def ensure_ocr_config(
    ocr: OCRConfig | None,
    models_cache_path: PathLike | str | None,
    local_only: bool,
) -> OCRConfig:
    if ocr is not None:
        if models_cache_path is not None or local_only:
            raise ValueError(
                "ocr cannot be combined with models_cache_path or local_only."
            )
        return ocr
    return LocalDeepSeekOCRConfig(
        models_cache_path=models_cache_path,
        local_only=local_only,
    )


def _env_name(name: str, fallback_name: str | None = None) -> str:
    import os

    if os.environ.get(name, "").strip():
        return name
    if fallback_name and os.environ.get(fallback_name, "").strip():
        return fallback_name
    return name


def _env_str(
    name: str,
    fallback_name: str | None = None,
    default: str | None = None,
) -> str:
    import os

    actual_name = _env_name(name, fallback_name)
    value = os.environ.get(actual_name, "").strip()
    if value:
        return value
    if default is not None:
        return default
    return ""


def _required_env(name: str, fallback_name: str | None = None) -> str:
    actual_name = _env_name(name, fallback_name)
    value = _env_str(actual_name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {actual_name}")
    return value


def _env_bool(
    name: str,
    fallback_name: str | None = None,
    default: bool = False,
) -> bool:
    value = _env_str(name, fallback_name=fallback_name)
    if not value:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{_env_name(name, fallback_name)} must be a boolean, got {value!r}")


def _env_int(
    name: str,
    fallback_name: str | None = None,
    default: int = 0,
) -> int:
    value = _env_str(name, fallback_name=fallback_name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(
            f"{_env_name(name, fallback_name)} must be an integer, got {value!r}"
        ) from exc


def _env_float(
    name: str,
    fallback_name: str | None = None,
    default: float = 0.0,
) -> float:
    value = _env_str(name, fallback_name=fallback_name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(
            f"{_env_name(name, fallback_name)} must be a float, got {value!r}"
        ) from exc


def _env_optional_float(
    name: str,
    fallback_name: str | None = None,
) -> float | None:
    value = _env_str(name, fallback_name=fallback_name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(
            f"{_env_name(name, fallback_name)} must be a float, got {value!r}"
        ) from exc
