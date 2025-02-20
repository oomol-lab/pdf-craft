import os

from typing import cast
from pydantic import SecretStr
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

class LLM:
  def __init__(self, key: str, url: str, model: str):
    self._prompts: dict[str, str] = {}
    self._model = ChatOpenAI(
      api_key=cast(SecretStr, key),
      base_url=url,
      model=model,
      temperature=0.7,
    )

  def request(self, prompt_name: str, data: str):
    prompt_path = os.path.join(__file__, "..", "prompts", f"{prompt_name}.jinja")
    prompt_path = os.path.abspath(prompt_path)
    response = self._model([
      SystemMessage(content=self._prompt(prompt_name)),
      HumanMessage(content=data)
    ])
    return response.content

  def _prompt(self, prompt_name: str) -> str:
    prompt: str | None = self._prompts.get(prompt_name, None)
    if prompt is None:
      prompt_path = os.path.join(__file__, "..", "prompts", f"{prompt_name}.jinja")
      prompt_path = os.path.abspath(prompt_path)
      with open(prompt_path, mode="r", encoding="utf-8") as file:
        prompt = file.read()
        self._prompts[prompt_name] = prompt
    return prompt