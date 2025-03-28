from typing import cast, Any, Callable
from io import StringIO
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

    self._timeout: float | None = timeout
    self._temperatures: tuple[float, float] | None = temperatures
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
    temperature: float | None = None
    max_temperature: float | None = None
    result: Any | None = None

    if self._temperatures is not None:
      temperature, max_temperature = self._temperatures

    try:
      for i in range(self._retry_times + 1):
        try:
          response = self._invoke_model(
            input=input,
            temperature=temperature,
          )
        except Exception as err:
          last_error = err
          if not is_retry_error(err):
            raise err
          print(f"request failed with connection error, retrying... ({i + 1} times)")
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times:
            sleep(self._retry_interval_seconds)
          continue

        try:
          result = parser(response)
          break

        except Exception as err:
          last_error = err
          print(f"request failed with parsing error, retrying... ({i + 1} times)")
          if temperature is not None and max_temperature is not None:
            temperature = temperature + 0.5 * (max_temperature - temperature)
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times:
            sleep(self._retry_interval_seconds)
          continue

    except KeyboardInterrupt as err:
      if last_error is not None:
        print(last_error)
      raise err

    if last_error is not None:
      raise last_error
    return result

  def _invoke_model(self, input: LanguageModelInput, temperature: float | None):
    stream = self._model.stream(
      input=input,
      timeout=self._timeout,
      temperature=temperature,
    )
    buffer = StringIO()
    for chunk in stream:
      data = str(chunk.content)
      buffer.write(data)
    return buffer.getvalue()