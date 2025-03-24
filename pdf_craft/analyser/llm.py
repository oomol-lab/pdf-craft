from typing import cast, Any
from importlib.resources import files
from jinja2 import Environment, Template
from xml.etree.ElementTree import tostring, Element
from pydantic import SecretStr
from tiktoken import get_encoding, Encoding
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from ..template import create_env


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
    prompts_path = files("pdf_craft").joinpath("data/prompts")
    self._env: Environment = create_env(prompts_path)

  def request(self, template_name: str, xml_data: Element, params: dict[str, Any]) -> str:
    template = self._template(template_name)
    prompt = template.render(**params)
    data = tostring(xml_data, encoding="unicode")
    response = self._model.invoke([
      SystemMessage(content=prompt),
      HumanMessage(content=data)
    ])
    return response.content

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
