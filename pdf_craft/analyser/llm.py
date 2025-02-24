import os

from typing import cast
from dataclasses import dataclass
from pydantic import SecretStr
from tiktoken import Encoding
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


@dataclass
class _Prompt:
  content: str
  tokens: int

class LLM:
  def __init__(self, key: str, url: str, model: str):
    self._prompts: dict[str, _Prompt] = {}
    self._encoding: Encoding = Encoding("o200k_base") # pylint: disable=missing-kwoa
    self._model = ChatOpenAI(
      api_key=cast(SecretStr, key),
      base_url=url,
      model=model,
      temperature=0.7,
    )

  def request(self, prompt_name: str, data: str):
    prompt_path = os.path.join(__file__, "..", "prompts", f"{prompt_name}.jinja")
    prompt_path = os.path.abspath(prompt_path)
    prompt = self._prompt(prompt_name).content
    response = self._model([
      SystemMessage(content=prompt),
      HumanMessage(content=data)
    ])
    return response.content

  def prompt_tokens_count(self, prompt_name: str) -> int:
    return self._prompt(prompt_name).tokens

  def encode_tokens(self, text: str) -> list[int]:
    return self._encoding.encode(text)

  def decode_tokens(self, tokens: list[int]) -> str:
    return self._encoding.decode(tokens)

  def count_tokens_count(self, text: str) -> int:
    return len(self._encoding.encode(text))

  def _prompt(self, prompt_name: str) -> _Prompt:
    prompt: _Prompt | None = self._prompts.get(prompt_name, None)
    if prompt is None:
      prompt_path = os.path.join(__file__, "..", "prompts", f"{prompt_name}.jinja")
      prompt_path = os.path.abspath(prompt_path)
      with open(prompt_path, mode="r", encoding="utf-8") as file:
        file_content: str = file.read()
        prompt = _Prompt(
          content=file_content,
          tokens=self.count_tokens_count(file_content),
        )
        self._prompts[prompt_name] = prompt
    return prompt