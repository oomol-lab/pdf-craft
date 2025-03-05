import os
import re

from typing import Tuple, Callable
from jinja2 import select_autoescape, Environment, BaseLoader, TemplateNotFound

def create_env(dir_path: str) -> Environment:
  return Environment(
    loader=_DSLoader(dir_path),
    autoescape=select_autoescape(),
    trim_blocks=True,
    keep_trailing_newline=True,
  )

_LoaderResult = Tuple[str, str | None, Callable[[], bool] | None]

class _DSLoader(BaseLoader):
  def __init__(self, dir_path: str):
    super().__init__()
    self._dir_path: str = dir_path

  def get_source(self, _: Environment, template: str) -> _LoaderResult:
    template = self._norm_template(template)
    target_path = os.path.join(self._dir_path, template)
    target_path = os.path.abspath(target_path)

    if not os.path.exists(target_path):
      raise TemplateNotFound(f"cannot find {template}")

    return self._get_source_with_path(target_path)

  def _norm_template(self, template: str) -> str:
    if bool(re.match(r"^\.+/", template)):
      raise TemplateNotFound(f"invalid path {template}")

    template = re.sub(r"^/", "", template)
    template = re.sub(r"\.jinja$", "", template, flags=re.IGNORECASE)
    template = f"{template}.jinja"

    return template

  def _get_source_with_path(self, path: str) -> _LoaderResult:
    mtime = os.path.getmtime(path)
    with open(path, "r", encoding="utf-8") as f:
      source = f.read()

    def is_updated() -> bool:
      return mtime == os.path.getmtime(path)

    return source, path, is_updated