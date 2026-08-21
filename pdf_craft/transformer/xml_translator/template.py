import re
from collections.abc import Callable
from pathlib import Path

from jinja2 import BaseLoader, Environment, TemplateNotFound, select_autoescape


def create_env(dir_path: Path) -> Environment:
    return Environment(
        loader=_DSLoader(dir_path),
        autoescape=select_autoescape(),
        trim_blocks=True,
        keep_trailing_newline=True,
    )


_LoaderResult = tuple[str, str | None, Callable[[], bool] | None]


class _DSLoader(BaseLoader):
    def __init__(self, dir_path: Path):
        super().__init__()
        self._dir_path: Path = dir_path

    def get_source(self, environment: Environment, template: str) -> _LoaderResult:
        template = self._norm_template(template)
        target_path = (self._dir_path / template).resolve()

        if not target_path.exists():
            raise TemplateNotFound(f"cannot find {template}")

        return self._get_source_with_path(target_path)

    def _norm_template(self, template: str) -> str:
        if bool(re.match(r"^\.+/", template)):
            raise TemplateNotFound(f"invalid path {template}")

        template = re.sub(r"^/", "", template)
        template = re.sub(r"\.jinja$", "", template, flags=re.IGNORECASE)
        template = f"{template}.jinja"

        return template

    def _get_source_with_path(self, path: Path) -> _LoaderResult:
        mtime = path.stat().st_mtime
        with open(path, encoding="utf-8") as f:
            source = f.read()

        def is_updated() -> bool:
            return mtime == path.stat().st_mtime

        return source, str(path), is_updated
