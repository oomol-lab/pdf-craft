from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..context import Context
from ..utils import read_xml_file
from .common import Phase, State
from .collection import collect
from .utils import normalize_layout_xml


def extract_contents(llm: LLM, workspace: Path, sequence_path: Path, max_data_tokens: int):
  context: Context[State] = Context(workspace, lambda: {
    "phase": Phase.INIT,
    "page_indexes": [],
    "max_data_tokens": max_data_tokens,
  })
  extracted_path = context.path.joinpath("extracted.xml")
  extracted_xml: Element

  if Phase(context.state["phase"]) == Phase.COMPLETED:
    extracted_xml = read_xml_file(extracted_path)
  else:
    md_content = _extract_md_contents(
      llm=llm,
      context=context,
      sequence_path=sequence_path,
    )
    extracted_xml = llm.request_xml(
      template_name="contents/format",
      user_data=f"```Markdown\n{md_content}\n```",
    )
    context.write_xml_file(extracted_path, extracted_xml)
    context.state = {
      **context.state,
      "phase": Phase.COMPLETED.value,
    }

def _extract_md_contents(llm: LLM, context: Context[State], sequence_path: Path) -> str:
  md_path = context.path.joinpath("extracted.md")
  md_content: str

  if md_path.exists():
    context.write_xml_file
    with md_path.open("r", encoding="utf-8") as f:
      md_content = f.read()
  else:
    request_xml = Element("request")
    for paragraph in collect(llm, context, sequence_path):
      layout = normalize_layout_xml(paragraph)
      if layout is not None:
        request_xml.append(layout)

    md_content = llm.request_markdown(
      template_name="contents/extractor",
      user_data=request_xml,
    )
    context.atomic_write(md_path, md_content)

  return md_content