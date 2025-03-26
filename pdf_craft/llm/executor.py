from typing import cast, Callable, TypeVar
from time import sleep
from pydantic import SecretStr
from langchain_core.language_models import LanguageModelInput
from langchain_openai import ChatOpenAI
from .error import is_retry_error


R = TypeVar["R"]

class LLMExecutor:
  def __init__(
    self,
    api_key: SecretStr,
    url: str,
    model: str,
    temperatures: tuple[float, float],
    retry_times: int,
    retry_interval_seconds: float,
  ) -> None:
    self._temperatures: tuple[float, float] = temperatures
    self._retry_times: int = retry_times
    self._retry_interval_seconds: float = retry_interval_seconds
    self._model = ChatOpenAI(
      api_key=cast(SecretStr, api_key),
      base_url=url,
      model=model,
    )

  def request(self, input: LanguageModelInput, parser: Callable[[str], R]) -> R:
    last_error: Exception | None = None
    temperature, max_temperature = self._temperatures

    for i in range(self._retry_times):
      try:
        response = self._model.invoke(
          input=input,
          temperature=temperature,
        )
      except Exception as err:
        last_error = err
        if not is_retry_error(err):
          raise err
        if self._retry_interval_seconds > 0.0 and \
           i < self._retry_times - 1:
          sleep(self._retry_interval_seconds)
        continue

      try:
        result = parser(response.content)
      except Exception as err:
        last_error = err
        temperature = temperature + 0.5 * (max_temperature - temperature)
        if self._retry_interval_seconds > 0.0 and \
           i < self._retry_times - 1:
          sleep(self._retry_interval_seconds)
        continue

    if last_error is not None:
      raise last_error
    return result
