import re

from pathlib import Path
from strenum import StrEnum
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import clone as clone_xml
from ..reference import samples, NumberStyle
from ..utils import read_xml_file, Context
from .common import State


def repeat_multiple_correct(
      llm: LLM,
      context: Context[State],
      save_path: Path,
      raw_request: Element,
      is_footnote: bool,
    ) -> Element:

  save_path.mkdir(parents=True, exist_ok=True)
  repeater = _Repeater(
    llm=llm,
    context=context,
    save_path=save_path,
    is_footnote=is_footnote,
  )
  return repeater.do(raw_request)

_BASIC_RETRY_TIMES = 2
_FILE_NAME_PATTERN = re.compile(r"^step_(\d+)\.xml$")

class _Quality(StrEnum):
  PERFECT = "perfect"
  GOOD = "good"
  FAIR = "fair"
  POOR = "poor"
  INVALID = "invalid"

_STEPS_INCREMENT: tuple[tuple[_Quality, int], ...] = (
  (_Quality.INVALID, 5),
  (_Quality.POOR, 4),
  (_Quality.FAIR, 3),
  (_Quality.GOOD, 0),
  (_Quality.PERFECT, 0),
)

class _Repeater:
  def __init__(
        self,
        llm: LLM,
        context: Context[State],
        save_path: Path,
        is_footnote: bool,
      ):
    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._save_path: Path = save_path
    self._is_footnote: bool = is_footnote
    self._quality: _Quality | None = None
    self._remain_steps: int = _BASIC_RETRY_TIMES
    self._next_index: int = 1

  def do(self, raw_request_element: Element) -> Element:
    request_element = self._recover_state(raw_request_element)
    while self._remain_steps > 0 and \
          self._quality != _Quality.PERFECT:

      resp_element = self._llm.request_xml(
        template_name="correction/multiple",
        user_data=request_element,
        params={
          "layouts_count": 4,
          "is_footnote": self._is_footnote,
          "marks": samples(NumberStyle.CIRCLED_NUMBER, 6),
        },
      )
      quality = self._read_quality_from_element(resp_element)
      self._report_quality(quality)
      updation_element = self._save_step_file(
        request_element=request_element,
        resp_element=resp_element,
      )
      request_element = self._update_request_element(
        raw_request_element=request_element,
        updation_element=updation_element,
      )

    return request_element

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

  def _read_quality_from_element(self, element: Element) -> _Quality:
    quality: _Quality = _Quality.PERFECT
    overview_element = element.find("overview")
    if overview_element is not None:
      quality = _Quality(overview_element.get("quality", _Quality.PERFECT.value))
    return quality

  def _report_quality(self, resp_quality: _Quality) -> None:
    if self._quality is None:
      self._quality = resp_quality

    else:
      found_begin = False
      found_end = False
      increment_remain_steps: int = 0

      for quality, increment in _STEPS_INCREMENT:
        if quality == self._quality:
          found_begin = True
        if quality == resp_quality:
          found_end = True
          break
        if found_begin:
          increment_remain_steps += increment

      if found_begin and found_end:
        self._remain_steps += increment_remain_steps
        self._quality = resp_quality

    self._remain_steps = max(0, self._remain_steps - 1)

  def _update_request_element(self, raw_request_element: Element, updation_element: Element) -> Element:
    # TODO: 此处可用传统逻辑设计些防御性操作，以避免 LLM 生成内容发生如下错误
    #       这些错误我虽然用 prompt 进行了约束，但无法杜绝：
    #         1. layout ID 错误，这将导致整个自然段丢失
    #         2. line 丢失某些行，或插入某些行
    #
    #       可以考虑用字符串匹配或 Sentence embedding 技术来发现这些问题，并用传统算法修复

    request_ids: list[str] = []
    request_layouts: dict[str, Element] = {}

    for layout_element in clone_xml(raw_request_element):
      layout_id = layout_element.get("id", None)
      if layout_id is not None:
        request_ids.append(layout_id)
        request_layouts[layout_id] = layout_element

    for updation_layout_element in updation_element:
      layout_id = updation_layout_element.get("id", None)
      if layout_id is None:
        continue
      request_layout_element = request_layouts.get(layout_id, None)
      if request_layout_element is None:
        continue
      raw_attrib = request_layout_element.attrib
      request_layout_element.clear()
      request_layout_element.extend(updation_layout_element)
      request_layout_element.attrib = raw_attrib

    request_element = Element("request")
    for request_id in request_ids:
      layout_element = request_layouts.get(request_id, None)
      if layout_element is not None:
        request_element.append(layout_element)

    return request_element
