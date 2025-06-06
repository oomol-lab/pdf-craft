from typing import cast, Any, Callable
from io import StringIO
from time import sleep
from pydantic import SecretStr
from logging import Logger
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.language_models import LanguageModelInput
from langchain_openai import ChatOpenAI

from .increasable import Increasable, Increaser
from .error import is_retry_error


class LLMExecutor:
  def __init__(
    self,
    api_key: SecretStr,
    url: str,
    model: str,
    timeout: float | None,
    top_p: Increasable,
    temperature: Increasable,
    retry_times: int,
    retry_interval_seconds: float,
    create_logger: Callable[[], Logger | None],
  ) -> None:

    self._timeout: float | None = timeout
    self._top_p: Increasable = top_p
    self._temperature: Increasable = temperature
    self._retry_times: int = retry_times
    self._retry_interval_seconds: float = retry_interval_seconds
    self._create_logger: Callable[[], Logger | None] = create_logger
    self._model = ChatOpenAI(
      api_key=cast(SecretStr, api_key),
      base_url=url,
      model=model,
      timeout=timeout,
    )

  def request(self, input: LanguageModelInput, parser: Callable[[str], Any]) -> Any:
    result: Any | None = None
    last_error: Exception | None = None
    did_success = False
    top_p: Increaser = self._top_p.context()
    temperature: Increaser = self._temperature.context()
    logger = self._create_logger()

    if logger is not None:
      logger.debug(f"[[Request]]:\n{self._input2str(input)}\n")

    try:
      for i in range(self._retry_times + 1):
        try:
          response = self._invoke_model(
            input=input,
            top_p=top_p.current,
            temperature=temperature.current,
          )
          if logger is not None:
            logger.debug(f"[[Response]]:\n{response}\n")

        except Exception as err:
          last_error = err
          if not is_retry_error(err):
            raise err
          if logger is not None:
            logger.warning(f"request failed with connection error, retrying... ({i + 1} times)")
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times:
            sleep(self._retry_interval_seconds)
          continue

        try:
          result = parser(response)
          did_success = True
          break

        except Exception as err:
          last_error = err
          warn_message = f"request failed with parsing error, retrying... ({i + 1} times)"
          if logger is not None:
            logger.warning(warn_message)
          print(warn_message)
          top_p.increase()
          temperature.increase()
          if self._retry_interval_seconds > 0.0 and \
            i < self._retry_times:
            sleep(self._retry_interval_seconds)
          continue

    except KeyboardInterrupt as err:
      if last_error is not None and logger is not None:
        logger.debug(f"[[Error]]:\n{last_error}\n")
      raise err

    if not did_success:
      if last_error is None:
        raise RuntimeError("Request failed with unknown error")
      else:
        raise last_error

    return result

  def _input2str(self, input: LanguageModelInput) -> str:
    if isinstance(input, str):
      return input
    if not isinstance(input, list):
      raise ValueError(f"Unsupported input type: {type(input)}")

    buffer = StringIO()
    is_first = True
    for message in input:
      if not is_first:
        buffer.write("\n\n")
      if isinstance(message, SystemMessage):
        buffer.write("System:\n")
        buffer.write(message.content)
      elif isinstance(message, HumanMessage):
        buffer.write("User:\n")
        buffer.write(message.content)
      elif isinstance(message, AIMessage):
        buffer.write("Assistant:\n")
        buffer.write(message.content)
      else:
        buffer.write(str(message))
      is_first = False

    return buffer.getvalue()

  def _invoke_model(
        self,
        input: LanguageModelInput,
        top_p: float | None,
        temperature: float | None,
      ):
    stream = self._model.stream(
      input=input,
      timeout=self._timeout,
      top_p=top_p,
      temperature=temperature,
    )
    buffer = StringIO()
    for chunk in stream:
      data = str(chunk.content)
      buffer.write(data)
    return buffer.getvalue()