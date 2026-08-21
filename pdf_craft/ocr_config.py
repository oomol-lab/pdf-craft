from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path
from typing import Iterable, Literal, TypeAlias

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


LocalOCRConfig: TypeAlias = (
    DeepSeekOCRLocalConfig | DeepSeekOCR2LocalConfig | UnlimitedOCRLocalConfig
)
VendorOCRConfig: TypeAlias = (
    DeepSeekOCRVendorConfig | DeepSeekOCR2VendorConfig | UnlimitedOCRVendorConfig
)
OCRConfig: TypeAlias = LocalOCRConfig | VendorOCRConfig
OCRMode: TypeAlias = Literal[
    "deepseek-ocr-local",
    "deepseek-ocr2-local",
    "unlimited-ocr-local",
    "deepseek-ocr-vendor",
    "deepseek-ocr2-vendor",
    "unlimited-ocr-vendor",
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
