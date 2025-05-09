from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import tostring, Element

from ..llm import LLM
from .context import Context
from .utils import search_xml_children

def to_sequences(llm: LLM, workspace: Path, ocr_path: Path) -> None:
  return _Sequence(llm, workspace).to_sequences(ocr_path)

class _Sequence:
  def __init__(self, llm: LLM, workspace: Path) -> None:
    self._llm: LLM = llm
    self._ctx: Context[None] = Context(workspace, lambda: None)

  def to_sequences(self, ocr_path: Path):
    raw_page_xmls: list[Element] = []
    for xml_path, _, page_index, _ in self._ctx.xml_files(ocr_path):
      raw_page_xml = self._ctx.read_xml_file(xml_path)
      raw_page_xml.set("page-index", str(page_index))
      raw_page_xmls.append(raw_page_xml)

    resp_xml = self._request_sequences(raw_page_xmls[:3])
    print(tostring(resp_xml, encoding="unicode"))

  def _request_sequences(self, raw_page_xmls: Iterable[Element]) -> Element:
    next_id: int = 1
    request_xml = Element("request")
    request_xml.extend(raw_page_xmls)

    for page in request_xml:
      for layout in page:
        for child, _ in search_xml_children(layout):
          child.set("id", str(next_id))
          next_id += 1

    return self._llm.request_xml(
      template_name="sequence",
      user_data=request_xml,
      params={},
    )