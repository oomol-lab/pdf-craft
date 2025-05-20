from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..context import Context
from ..sequence import read_paragraphs, Paragraph, ParagraphType
from .common import State, Phase


def correct(llm: LLM, workspace: Path, text_path: Path, footnote_path: Path, max_data_tokens: int):
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.Text,
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
      "phase": Phase.FOOTNOTE,
    }

  if context.state["phase"] == Phase.FOOTNOTE:
    corrector.do(
      from_path=footnote_path,
      request_path=workspace / "footnote",
    )
    context.state = {
      **context.state,
      "phase": Phase.COMPLETED,
    }

class _Corrector:
  def __init__(self, llm: LLM, context: Context[State]):
    self._llm: LLM = llm
    self._ctx: Context[State] = context

  def do(self, from_path: Path, request_path: Path):
    for request_element in self._generate_request_xml(from_path, request_path):
      self._llm.request_xml(
        template_name="correction",
        user_data=request_element,
      )

  def _generate_request_xml(self, from_path: Path, request_path: Path):
    request_path.mkdir(parents=True, exist_ok=True)
    max_data_tokens = self._ctx.state["max_data_tokens"]
    request_element = Element("request")
    data_tokens: int = 0
    next_line_id: int = 1
    last_type: ParagraphType | None = None

    for paragraph in read_paragraphs(from_path):
      next_line_id, layout_element = self._paragraph_to_layout_xml(
        paragraph=paragraph,
        begin_line_id=next_line_id,
      )
      tokens = self._llm.count_tokens_count(
        text=encode_friendly(layout_element),
      )
      if len(request_element) > 0 and (
        data_tokens + tokens > max_data_tokens or
        last_type != paragraph.type
      ):
        yield request_element
        request_element = Element("request")
        data_tokens = 0
        next_line_id, layout_element = self._paragraph_to_layout_xml(
          paragraph=paragraph,
          begin_line_id=1,
        )
      request_element.append(layout_element)
      data_tokens += tokens
      last_type = paragraph.type

    if len(request_element) > 0:
      yield request_element

  def _paragraph_to_layout_xml(self, paragraph: Paragraph, begin_line_id: int) -> tuple[int, Element]:
    layout_element: Element | None = None
    next_line_id: int = begin_line_id

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
    return next_line_id, layout_element