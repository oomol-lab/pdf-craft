from os import PathLike
from pathlib import Path

from jinja2 import Template
from tiktoken import Encoding, get_encoding


class LLM:
    """Declarative LLM configuration; execution is provided by ``LLMRuntime``."""

    def __init__(self, key: str, url: str, model: str, token_encoding: str,
                 timeout: float | None = None,
                 top_p: float | tuple[float, float] | None = None,
                 temperature: float | tuple[float, float] | None = None,
                 retry_times: int = 5, retry_interval_seconds: float = 6.0,
                 cache_path: PathLike | str | None = None,
                 log_dir_path: PathLike | str | None = None) -> None:
        self.key, self.url, self.model, self.token_encoding = key, url, model, token_encoding
        self.timeout, self.top_p, self.temperature = timeout, top_p, temperature
        self.retry_times, self.retry_interval_seconds = retry_times, retry_interval_seconds
        self.cache_path = _directory(cache_path)
        self.log_dir_path = _directory(log_dir_path)
        self._encoding = get_encoding(token_encoding)
        self._templates: dict[str, Template] = {}

    @property
    def encoding(self) -> Encoding:
        return self._encoding

    def template(self, template_name: str) -> Template:
        template = self._templates.get(template_name)
        if template is None:
            path = Path(__file__).parent.parent / "transformer" / "xml_translator" / "data" / f"{template_name}.jinja"
            template = Template(path.read_text(encoding="utf-8"))
            self._templates[template_name] = template
        return template


def _directory(path: PathLike | str | None) -> Path | None:
    if path is None:
        return None
    result = Path(path)
    result.mkdir(parents=True, exist_ok=True)
    return result.resolve() if result.is_dir() else None
