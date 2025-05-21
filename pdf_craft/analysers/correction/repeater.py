import re

from pathlib import Path
from strenum import StrEnum
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..utils import read_xml_file
from ..context import Context
from .common import State


_REPEAT_COUNT = 4
_FILE_NAME_PATTERN = re.compile(r"^step_(\d+)\.xml$")

def repeat_correct(llm: LLM, context: Context[State], save_path: Path, raw_request: Element) -> None:
  save_path.mkdir(parents=True, exist_ok=True)
  repeater = _Repeater(
    llm=llm,
    context=context,
    save_path=save_path,
  )
  return repeater.do(raw_request)

class _Quality(StrEnum):
  PERFECT = "perfect"
  GOOD = "good"
  FAIR = "fair"
  POOR = "poor"
  INVALID = "invalid"

class _Repeater:
  def __init__(self, llm: LLM, context: Context[State], save_path: Path):
    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._save_path: Path = save_path
    self._quality: _Quality = _Quality.INVALID
    self._remain_steps: int = self._quality_retry_times(self._quality)
    self._next_index: int = 1

  def do(self, raw_request_element: Element):
    request_element = self._recover_state(raw_request_element)

    while self._remain_steps > 0:
      resp_element = self._llm.request_xml(
        template_name="correction",
        user_data=request_element,
        params={"layouts_count": 3},
      )
      quality = self._read_quality_from_element(resp_element)
      self._report_quality(quality)
      updation_element = self._save_step_file(
        request_element=request_element,
        resp_element=resp_element,
      )
      request_element = self._update_request_element(updation_element)

  def _recover_state(self, raw_request_element: Element) -> Element:
    request_element: Element = raw_request_element
    files: list[tuple[int, Path]] = []

    for file in self._save_path.iterdir():
      match = _FILE_NAME_PATTERN.match(file.name)
      if match:
        index = int(match.group(1))
        files.append((index, file))

    for index, file in sorted(files, key=lambda x: x[0]):
      file_element = read_xml_file(file)
      request_element = file_element.find("request") or request_element
      file_quality = self._read_quality_from_element(file_element)
      self._report_quality(file_quality)
      self._next_index = index + 1

    return request_element

  def _save_step_file(self, request_element: Element, resp_element: Element) -> Element:
    file_element = Element("correction")
    overview_element = resp_element.find("overview")
    if overview_element is not None:
      resp_element.remove(overview_element)
      file_element.append(overview_element)

    if len(resp_element) > 0:
      resp_element.tag = "updation"
      file_element.append(resp_element)
      file_element.append(request_element)

    file_name = f"step_{self._next_index}.xml"
    file_path = self._save_path / file_name
    self._ctx.write_xml_file(file_path, file_element)
    self._next_index += 1

    return resp_element

  def _update_request_element(self, request_element: Element) -> Element:
    return request_element

  def _read_quality_from_element(self, element: Element) -> _Quality:
    quality: _Quality = _Quality.PERFECT
    overview_element = element.find("overview")
    if overview_element is not None:
      quality = _Quality(overview_element.get("quality", _Quality.PERFECT.value))
    return quality

  def _report_quality(self, resp_quality: _Quality) -> None:
    resp_order = self._quality_order(resp_quality)
    self_order = self._quality_order(self._quality)

    if resp_order >= self_order:
      self._remain_steps = max(0, self._remain_steps - 1)
    else:
      self._quality = resp_quality
      self._remain_steps = self._quality_retry_times(resp_quality)

  def _quality_retry_times(self, quality: _Quality) -> int:
    if quality == _Quality.PERFECT:
      return 0
    elif quality == _Quality.GOOD:
      return 1
    elif quality == _Quality.FAIR:
      return 2
    elif quality == _Quality.POOR:
      return 2
    else:
      return 4

  def _quality_order(self, quality: _Quality) -> int:
    if quality == _Quality.PERFECT:
      return 0
    elif quality == _Quality.GOOD:
      return 1
    elif quality == _Quality.FAIR:
      return 2
    elif quality == _Quality.POOR:
      return 3
    else:
      return 4