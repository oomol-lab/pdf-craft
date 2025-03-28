import re
import json

from typing import cast, Any
from importlib.resources import files
from jinja2 import Environment, Template
from xml.etree.ElementTree import tostring, fromstring, Element
from pydantic import SecretStr
from tiktoken import get_encoding, Encoding
from langchain_core.messages import SystemMessage, HumanMessage

from ..template import create_env
from .executor import LLMExecutor


class LLM:
  def __init__(
      self,
      key: str,
      url: str,
      model: str,
      token_encoding: str,
      timeout: float | None = None,
      temperature: float | tuple[float, float] | None = None,
      retry_times: int = 5,
      retry_interval_seconds: float = 6.0,
    ):

    if isinstance(temperature, float):
      temperature = (temperature, temperature)
    prompts_path = files("pdf_craft").joinpath("data/prompts")

    self._templates: dict[str, Template] = {}
    self._encoding: Encoding = get_encoding(token_encoding)
    self._env: Environment = create_env(prompts_path)
    self._executor = LLMExecutor(
      url=url,
      model=model,
      api_key=cast(SecretStr, key),
      timeout=timeout,
      temperatures=temperature,
      retry_times=retry_times,
      retry_interval_seconds=retry_interval_seconds,
    )

  def request_json(self, template_name: str, user_data: Element, params: dict[str, Any] | None = None) -> Any:
    if params is None:
      params = {}
    return self._executor.request(
      input=self._create_input(template_name, user_data, params),
      parser=self._encode_json,
    )

  def request_xml(self, template_name: str, user_data: Element, params: dict[str, Any] | None = None) -> Element:
    if params is None:
      params = {}
    return self._executor.request(
      input=self._create_input(template_name, user_data, params),
      parser=self._encode_xml,
    )

  def _create_input(self, template_name: str, user_data: Element, params: dict[str, Any]):
    template = self._template(template_name)
    data = tostring(user_data, encoding="unicode")
    prompt = template.render(**params)
    return [
      SystemMessage(content=prompt),
      HumanMessage(content=data)
    ]

  def prompt_tokens_count(self, template_name: str, params: dict[str, Any]) -> int:
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

  def _encode_json(self, response: str) -> Any:
    response = re.sub(r"^```JSON", "", response)
    response = re.sub(r"```$", "", response)
    try:
      return json.loads(response)
    except Exception as e:
      print(response)
      raise e

  def _encode_xml(self, response: str) -> Element:
    response = re.sub(r"^```XML", "", response)
    response = re.sub(r"```$", "", response)
    try:
      return fromstring(response.replace("&", "&amp;"))
    except Exception as e:
      print(response)
      raise e
