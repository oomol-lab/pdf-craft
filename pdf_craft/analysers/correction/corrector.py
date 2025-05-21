import sys
import shutil

from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..context import Context
from ..partition import Partition
from ..sequence import read_paragraphs, Paragraph, ParagraphType
from .common import State, Phase
from .repeater import repeat_correct


def correct(llm: LLM, workspace: Path, text_path: Path, footnote_path: Path, max_data_tokens: int):
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.Text.value,
    "max_data_tokens": max_data_tokens,
    "completed_ranges": [],
  })
  corrector = _Corrector(llm, context)

  if context.state["phase"] == Phase.Text:
    corrector.do(
      from_path=text_path,
      request_path=workspace / "text",
    )
    context.state = {
      **context.state,
      "phase": Phase.FOOTNOTE.value,
      "completed_ranges": [],
    }

  if context.state["phase"] == Phase.FOOTNOTE:
    corrector.do(
      from_path=footnote_path,
      request_path=workspace / "footnote",
    )
    context.state = {
      **context.state,
      "phase": Phase.COMPLETED.value,
      "completed_ranges": [],
    }

class _Corrector:
  def __init__(self, llm: LLM, context: Context[State]):
    self._llm: LLM = llm
    self._ctx: Context[State] = context

  def do(self, from_path: Path, request_path: Path):
    request_path.mkdir(parents=True, exist_ok=True)
    partition: Partition[tuple[int, int], State, Element] = Partition(
      dimension=2,
      context=self._ctx,
      sequence=self._generate_request_xml(from_path),
      remove=lambda begin, end: shutil.rmtree(
        request_path / self._step_dir_name(begin, end),
      ),
    )
    with partition:
      for task in partition.pop_tasks():
        with task:
          begin = task.begin
          end = task.end
          resp_element = repeat_correct(
            llm=self._llm,
            context=self._ctx,
            save_path=request_path / self._step_dir_name(begin, end),
            raw_request=task.payload,
          )

  def _generate_request_xml(self, from_path: Path):
    max_data_tokens = self._ctx.state["max_data_tokens"]
    request_element = Element("request")
    request_begin: tuple[int, int] = (sys.maxsize, sys.maxsize)
    request_end: tuple[int, int] = (-1, -1)
    data_tokens: int = 0
    last_type: ParagraphType | None = None

    for paragraph in read_paragraphs(from_path):
      layout_element = self._paragraph_to_layout_xml(paragraph)
      tokens = self._llm.count_tokens_count(
        text=encode_friendly(layout_element),
      )
      if len(request_element) > 0 and (
        data_tokens + tokens > max_data_tokens or
        last_type != paragraph.type
      ):
        yield request_begin, request_end, request_element
        request_element = Element("request")
        data_tokens = 0
        request_begin = (sys.maxsize, sys.maxsize)
        request_end = (-1, -1)
        layout_element = self._paragraph_to_layout_xml(paragraph)

      paragraph_index = (paragraph.page_index, paragraph.order_index)
      request_element.append(layout_element)
      request_begin = min(request_begin, paragraph_index)
      request_end = max(request_end, paragraph_index)
      data_tokens += tokens
      last_type = paragraph.type

    if len(request_element) > 0:
      yield request_begin, request_end, request_element

  def _paragraph_to_layout_xml(self, paragraph: Paragraph) -> tuple[int, Element]:
    layout_element: Element | None = None
    next_line_id: int = 1

    for layout in paragraph.layouts:
      if layout_element is None:
        layout_element = Element(layout.kind.value)
        layout_element.set("id", layout.id)
      for line in layout.lines:
        line_element = Element("line")
        line_element.set("id", str(next_line_id))
        line_element.text = line.text.strip()
        layout_element.append(line_element)
        next_line_id += 1

    assert layout_element is not None
    return layout_element

  def _step_dir_name(self, begin: tuple[int, int], end: tuple[int, int]) -> str:
    return f"steps_{begin[0]}_{begin[1]}_{end[0]}_{end[1]}"
