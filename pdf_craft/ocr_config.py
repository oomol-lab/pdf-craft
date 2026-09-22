import math
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Iterable, Literal, TypeAlias
from urllib.parse import urlsplit

from .to_path import to_path


@dataclass(frozen=True)
class DeepSeekOCRLocalConfig:
    models_cache_path: Path | None = None
    local_only: bool = False
    enable_devices_numbers: tuple[int, ...] | None = None

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
        object.__setattr__(
            self,
            "enable_devices_numbers",
            tuple(enable_devices_numbers) if enable_devices_numbers is not None else None,
        )


@dataclass(frozen=True)
class DeepSeekOCR2LocalConfig:
    models_cache_path: Path | None = None
    local_only: bool = False
    enable_devices_numbers: tuple[int, ...] | None = None

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
        object.__setattr__(
            self,
            "enable_devices_numbers",
            tuple(enable_devices_numbers) if enable_devices_numbers is not None else None,
        )


@dataclass(frozen=True)
class UnlimitedOCRLocalConfig:
    models_cache_path: Path | None = None
    local_only: bool = False
    enable_devices_numbers: tuple[int, ...] | None = None

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
        object.__setattr__(
            self,
            "enable_devices_numbers",
            tuple(enable_devices_numbers) if enable_devices_numbers is not None else None,
        )


@dataclass(frozen=True)
class DeepSeekOCRVendorConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int = 8000
    timeout_seconds: int = 180


@dataclass(frozen=True)
class DeepSeekOCR2VendorConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int = 8000
    timeout_seconds: int = 180


@dataclass(frozen=True)
class UnlimitedOCRVendorConfig:
    ak: str = field(repr=False)
    sk: str = field(repr=False)
    base_url: str = "https://aip.baidubce.com"
    poll_interval_seconds: float = 2.0
    timeout_seconds: int = 180


_DEFAULT_GLM_OCR_ENDPOINT_URL = "http://127.0.0.1:5002/glmocr/parse"


@dataclass(frozen=True)
class GLMOCRServiceConfig:
    endpoint_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 180

    def __post_init__(self) -> None:
        endpoint_url = self.endpoint_url
        if endpoint_url is None:
            endpoint_url = _DEFAULT_GLM_OCR_ENDPOINT_URL
        if not isinstance(endpoint_url, str) or not endpoint_url.strip():
            raise ValueError("endpoint_url must be a non-empty http(s) URL")
        endpoint_url = endpoint_url.strip()
        try:
            parsed = urlsplit(endpoint_url)
            _ = parsed.port
        except ValueError as error:
            raise ValueError("endpoint_url must be a non-empty http(s) URL") from error
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("endpoint_url must be a non-empty http(s) URL")
        object.__setattr__(self, "endpoint_url", endpoint_url)

        timeout_seconds = self.timeout_seconds
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        try:
            numeric_timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                "timeout_seconds must be a finite positive number"
            ) from error
        if not math.isfinite(numeric_timeout) or numeric_timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")


LocalOCRConfig: TypeAlias = (
    DeepSeekOCRLocalConfig | DeepSeekOCR2LocalConfig | UnlimitedOCRLocalConfig
)
VendorOCRConfig: TypeAlias = (
    DeepSeekOCRVendorConfig | DeepSeekOCR2VendorConfig | UnlimitedOCRVendorConfig
)
ServiceOCRConfig: TypeAlias = GLMOCRServiceConfig
OCRConfig: TypeAlias = LocalOCRConfig | VendorOCRConfig | ServiceOCRConfig
OCRMode: TypeAlias = Literal[
    "deepseek-ocr-local",
    "deepseek-ocr2-local",
    "unlimited-ocr-local",
    "deepseek-ocr-vendor",
    "deepseek-ocr2-vendor",
    "unlimited-ocr-vendor",
    "glm-ocr-service",
]


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
    return DeepSeekOCRLocalConfig(
        models_cache_path=models_cache_path,
        local_only=local_only,
    )
