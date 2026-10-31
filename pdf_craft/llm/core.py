from ..request_events import validate_callback, RequestCallback

import math
from os import PathLike
from pathlib import Path
from threading import Lock

from jinja2 import Template
from tiktoken import Encoding


class LLM:
    """Declarative LLM configuration; execution is provided by ``LLMRuntime``."""

    def __init__(self, key: str, url: str, model: str, token_encoding: str,
                 timeout: float | None = None,
                 top_p: float | tuple[float, float] | None = None,
                 temperature: float | tuple[float, float] | None = None,
                 retry_times: int = 5, retry_interval_seconds: float = 6.0,
                 cache_path: PathLike | str | None = None,
                 log_dir_path: PathLike | str | None = None, *,
                 failure_log_path: PathLike | str | None = None,
                 total_timeout: float | None = 360.0,
                 cleanup_timeout: float = 5.0,
                 on_request: RequestCallback | None = None) -> None:
        validate_callback(on_request)
        self.on_request = on_request
        for name, value in (("total_timeout", total_timeout), ("cleanup_timeout", cleanup_timeout)):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be a finite positive number")
        self.total_timeout = total_timeout
        self.cleanup_timeout = cleanup_timeout
        self.key, self.url, self.model, self.token_encoding = key, url, model, token_encoding
        self.timeout, self.top_p, self.temperature = timeout, top_p, temperature
        self.retry_times, self.retry_interval_seconds = retry_times, retry_interval_seconds
        self.cache_path = _directory(cache_path)
        self.log_dir_path = _directory(log_dir_path)
        self.failure_log_path = _directory(failure_log_path)
        self._encoding: Encoding | None = None
        self._templates: dict[str, Template] = {}
        self._resource_lock = Lock()


def _directory(path: PathLike | str | None) -> Path | None:
    if path is None:
        return None
    result = Path(path).expanduser()
    if not result.is_absolute():
        result = Path.cwd() / result
    return result
