from os import PathLike
from pathlib import Path
from threading import Lock

from jinja2 import Template
from tiktoken import Encoding, get_encoding

from ..runtime import IO_DOMAIN


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
        self._encoding: Encoding | None = None
        self._templates: dict[str, Template] = {}
        self._resource_lock = Lock()

    @property
    def encoding(self) -> Encoding:
        return self._load_encoding()

    async def _encoding_async(self) -> Encoding:
        """Load tiktoken's first-use resources outside the event loop."""
        return await IO_DOMAIN.run(self._load_encoding)

    def _load_encoding(self) -> Encoding:
        with self._resource_lock:
            if self._encoding is None:
                self._encoding = get_encoding(self.token_encoding)
            return self._encoding

    def template(self, template_name: str) -> Template:
        return self._load_template(template_name)

    async def _template_async(self, template_name: str) -> Template:
        """Read and compile a prompt template on the filesystem domain."""
        return await IO_DOMAIN.run(self._load_template, template_name)

    def _load_template(self, template_name: str) -> Template:
        template = self._templates.get(template_name)
        if template is None:
            with self._resource_lock:
                template = self._templates.get(template_name)
                if template is None:
                    path = Path(__file__).parent.parent / "transformer" / "xml_translator" / "data" / f"{template_name}.jinja"
                    template = Template(path.read_text(encoding="utf-8"))
                    self._templates[template_name] = template
        return template


def _directory(path: PathLike | str | None) -> Path | None:
    if path is None:
        return None
    result = Path(path).expanduser()
    if not result.is_absolute():
        result = Path.cwd() / result
    return result
