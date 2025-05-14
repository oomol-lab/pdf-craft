from pathlib import Path

from ...llm import LLM
from ...xml import encode
from ..context import Context
from .common import Phase, State
from .collection import collect


def extract_contents(llm: LLM, workspace: Path, sequence_path: Path, max_data_tokens: int):
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.INIT,
    "page_indexes": [],
    "max_data_tokens": max_data_tokens,
  })
  for paragraph in collect(llm, context, sequence_path):
    print(encode(paragraph.xml()))