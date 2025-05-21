import re

from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..utils import read_xml_file
from ..context import Context
from .common import State


_REPEAT_COUNT = 1

def repeat_correct(llm: LLM, context: Context[State], save_path: Path, raw_request: Element) -> None:
  save_path.mkdir(parents=True, exist_ok=True)
  repeater = _Repeater(
    llm=llm,
    context=context,
    save_path=save_path,
  )
  pattern = re.compile(r"^step_(\d+)\.xml$")
  request_element: Element = raw_request
  next_step = 1

  for file in save_path.iterdir():
    match = pattern.match(file.name)
    if match:
      index = int(match.group(1))
      next_step = max(next_step, index + 1)

  if next_step <= 1:
    request_element = raw_request
  else:
    file_path = save_path / f"step{next_step - 1}.xml"
    file_element = read_xml_file(file_path)
    request_element = file_element.find("step")

  return repeater.do(
    request_element=request_element,
    next_step=next_step,
  )

class _Repeater:
  def __init__(self, llm: LLM, context: Context[State], save_path: Path):
    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._save_path: Path = save_path

  def do(self, request_element: Element, next_step: int):
    for step in range(next_step, _REPEAT_COUNT + 1):
      resp_element = self._llm.request_xml(
        template_name="correction",
        user_data=request_element,
      )
      file_name = f"step_{step}.xml"
      file_path = self._save_path / file_name
      file_element = Element("correction")
      file_element.append(request_element)
      file_element.append(resp_element)
      self._ctx.write_xml_file(file_path, file_element)