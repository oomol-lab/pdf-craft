from typing import cast, Any, Callable
from time import sleep
from pydantic import SecretStr
from langchain_core.language_models import LanguageModelInput
from langchain_openai import ChatOpenAI
from .error import is_retry_error


class LLMExecutor:
  def __init__(
    self,
    api_key: SecretStr,
    url: str,
    model: str,
    timeout: float | None,
    temperatures: tuple[float, float] | None,
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
      timeout=timeout,
    )

  def request(self, input: LanguageModelInput, parser: Callable[[str], Any]) -> Any:
    last_error: Exception | None = None
    temperature, max_temperature = self._temperatures
    result: Any | None = None

    try:
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
          print(f"request failed with connection error, retrying... ({i + 1} times)")
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times - 1:
            sleep(self._retry_interval_seconds)
          continue

        try:
          result = parser(response.content)
        except Exception as err:
          last_error = err
          temperature = temperature + 0.5 * (max_temperature - temperature)
          print(f"request failed with parsing error, retrying... ({i + 1} times)")
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times - 1:
            sleep(self._retry_interval_seconds)
          continue

    except KeyboardInterrupt as err:
      if last_error is not None:
        print(last_error)
      raise err

    if last_error is not None:
      raise last_error
    return result
