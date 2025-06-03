import re

from pathlib import Path
from ...llm import LLM
from ..sequence import decode_paragraph, ParagraphWriter
from ..reporter import Reporter, AnalysingStep
from ..utils import read_xml_file, Context, MultiThreads
from .common import State, Phase, Level, Corrector
from .single_corrector import SingleCorrector
from .multiple_corrector import MultipleCorrector


def correct(
      llm: LLM,
      reporter: Reporter,
      threads: MultiThreads,
      level: Level,
      workspace_path: Path,
      text_path: Path,
      footnote_path: Path,
      max_data_tokens: int,
    ) -> Path:

  context: Context[State] = Context(
    reporter=reporter,
    path=workspace_path,
    init=lambda: {
      "phase": Phase.Text.value,
      "level": level.value,
      "max_data_tokens": max_data_tokens,
      "completed_ranges": [],
    },
  )
  corrector: Corrector
  output_path = workspace_path / "output"
  text_request_path = workspace_path / "text"
  footnote_request_path = workspace_path / "footnote"

  if context.state["level"] == Level.Single:
    corrector = SingleCorrector(llm, context, threads)
  elif context.state["level"] == Level.Multiple:
    corrector = MultipleCorrector(llm, context, threads)
  else:
    raise ValueError(f"Unknown level: {context.state['level']}")

  if context.state["phase"] == Phase.Text:
    if text_path.exists():
      reporter.go_to_step(AnalysingStep.CORRECT_TEXT)
      corrector.do(
        from_path=text_path,
        request_path=text_request_path,
        is_footnote=False,
      )
    context.state = {
      **context.state,
      "phase": Phase.FOOTNOTE.value,
      "completed_ranges": [],
    }

  if context.state["phase"] == Phase.FOOTNOTE:
    if footnote_path.exists():
      reporter.go_to_step(AnalysingStep.CORRECT_FOOTNOTE)
      corrector.do(
        from_path=footnote_path,
        request_path=footnote_request_path,
        is_footnote=True,
      )
    context.state = {
      **context.state,
      "phase": Phase.GENERATION.value,
      "completed_ranges": [],
    }

  if context.state["phase"] == Phase.GENERATION:
    if text_request_path.exists():
      _generate_paragraph_files(
        context=context,
        request_path=text_request_path,
        output_path=output_path / "text",
      )
    if footnote_request_path.exists():
      _generate_paragraph_files(
        context=context,
        request_path=footnote_request_path,
        output_path=output_path / "footnote",
      )
    context.state = {
      **context.state,
      "phase": Phase.COMPLETED.value,
    }
  return output_path

_CHUNK_FILE_PATTERN = re.compile(r"^chunk(_\d+){4}\.xml$")
_CHUNK_FILE_HEAD_AND_TAIL_PATTERN = re.compile(r"(^chunk_|\.xml$)")

def _generate_paragraph_files(context: Context[State], request_path: Path, output_path: Path):
  index_and_file_list: list[tuple[tuple[int, int], tuple[int, int], Path]] = []
  for file in request_path.iterdir():
    matches = re.match(_CHUNK_FILE_PATTERN, file.name)
    if not matches:
      continue
    indexes_text = re.sub(_CHUNK_FILE_HEAD_AND_TAIL_PATTERN, "", file.name)
    indexes = list(int(i) for i in indexes_text.split("_"))
    index1 = (indexes[0], indexes[1])
    index2 = (indexes[2], indexes[3])
    index_and_file_list.append((index1, index2, file))

  writer = ParagraphWriter(context, output_path)
  for _, _, file in sorted(index_and_file_list, key=lambda x: x[0]):
    for paragraph_element in read_xml_file(file):
      paragraph = decode_paragraph(
        element=paragraph_element,
        page_index=int(paragraph_element.get("page-index", "-1")),
        order_index=int(paragraph_element.get("order-index", "-1")),
      )
      writer.write(paragraph)
