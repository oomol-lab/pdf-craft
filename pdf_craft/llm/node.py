import re
import json

from os import PathLike
from typing import cast, Any
from importlib.resources import files
from jinja2 import Environment, Template
from xml.etree.ElementTree import Element
from pydantic import SecretStr
from logging import getLogger, DEBUG, Formatter, Logger, FileHandler
from tiktoken import get_encoding, Encoding
from langchain_core.messages import SystemMessage, HumanMessage

from ..template import create_env
from ..xml import decode as decode_xml, encode as encode_xml
from .increasable import Increasable
from .executor import LLMExecutor


class LLM:
  def __init__(
      self,
      key: str,
      url: str,
      model: str,
      token_encoding: str,
      timeout: float | None = None,
      top_p: float | tuple[float, float] | None = None,
      temperature: float | tuple[float, float] | None = None,
      retry_times: int = 5,
      retry_interval_seconds: float = 6.0,
      log_file_path: PathLike | None = None,
    ):
    prompts_path = files("pdf_craft").joinpath("data/prompts")
    self._templates: dict[str, Template] = {}
    self._encoding: Encoding = get_encoding(token_encoding)
    self._env: Environment = create_env(prompts_path)
    self._logger: Logger | None = None
    if log_file_path is not None:
      self._logger = self._create_logger(log_file_path)

    self._executor = LLMExecutor(
      url=url,
      model=model,
      api_key=cast(SecretStr, key),
      timeout=timeout,
      top_p=Increasable(top_p),
      temperature=Increasable(temperature),
      retry_times=retry_times,
      retry_interval_seconds=retry_interval_seconds,
      logger=self._logger,
    )

  def _create_logger(self, log_file_path: PathLike) -> Logger:
    logger = getLogger("LLM Request")
    logger.setLevel(DEBUG)
    handler = FileHandler(log_file_path, encoding="utf-8")
    handler.setLevel(DEBUG)
    handler.setFormatter(Formatter("%(asctime)s    %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    return logger

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
    data = encode_xml(user_data)
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
    return json.loads(response)

  def _encode_xml(self, response: str) -> Element:
    for element in decode_xml(response, "response"):
      return element
    raise ValueError("No valid XML response found")