from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..context import Context
from .common import Phase, State
from .collection import collect
from .utils import normalize_layout_xml


def extract_contents(llm: LLM, workspace: Path, sequence_path: Path, max_data_tokens: int):
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.INIT,
    "page_indexes": [],
    "max_data_tokens": max_data_tokens,
  })
  request_xml = Element("request")
  for paragraph in collect(llm, context, sequence_path):
    layout = normalize_layout_xml(paragraph)
    if layout is not None:
      request_xml.append(layout)

  resp_json = llm.request_xml(
    template_name="contents_extractor",
    user_data=request_xml,
  )
  print(resp_json)