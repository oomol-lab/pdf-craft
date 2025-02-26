import os
import re

from typing import cast, Any, Tuple, Callable
from jinja2 import select_autoescape, Environment, BaseLoader, Template, TemplateNotFound
from pydantic import SecretStr
from tiktoken import get_encoding, Encoding
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


class LLM:
  def __init__(
      self,
      key: str,
      url: str,
      model: str,
      token_encoding: str,
    ):
    self._templates: dict[str, Template] = {}
    self._encoding: Encoding = get_encoding(token_encoding)
    self._model = ChatOpenAI(
      api_key=cast(SecretStr, key),
      base_url=url,
      model=model,
      temperature=0.7,
    )
    prompts_path = os.path.join(__file__, "..", "prompts")
    prompts_path = os.path.abspath(prompts_path)

    self._env: Environment = Environment(
      loader=_DSLoader(prompts_path),
      autoescape=select_autoescape(),
      trim_blocks=True,
      keep_trailing_newline=True,
    )

  def request(self, template_name: str, data: str, params: dict[str, Any] = {}) -> str:
    template = self._template(template_name)
    prompt = template.render(**params)
    response = self._model([
      SystemMessage(content=prompt),
      HumanMessage(content=data)
    ])
    return response.content

  def prompt_tokens_count(self, template_name: str, params: dict[str, Any] = {}) -> int:
    template = self._template(template_name)
    prompt = template.render(**params)
    return len(self._encoding.encode(prompt))

  def encode_tokens(self, text: str) -> list[int]:
    return self._encoding.encode(text)

  def decode_tokens(self, tokens: list[int]) -> str:
    return self._encoding.decode(tokens)

  def count_tokens_count(self, text: str) -> int:
    return len(self._encoding.encode(text))

  def _template(self, template_name: str) -> Template:
    template = self._templates.get(template_name, None)
    if template is None:
      template = self._env.get_template(template_name)
      self._templates[template_name] = template
    return template

_LoaderResult = Tuple[str, str | None, Callable[[], bool] | None]

class _DSLoader(BaseLoader):
  def __init__(self, prompts_path: str):
    super().__init__()
    self._prompts_path: str = prompts_path

  def get_source(self, _: Environment, template: str) -> _LoaderResult:
    template = self._norm_template(template)
    target_path = os.path.join(self._prompts_path, template)
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