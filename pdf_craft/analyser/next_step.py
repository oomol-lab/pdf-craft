from __future__ import annotations
from typing import Generator
from xml.etree.ElementTree import tostring, Element

from ..llm import LLM


def analyse_next_step(llm: LLM, raw_page_xmls: tuple[int, Element]):
  request_xml = Element("request")
  for page_index, raw_page_xml in raw_page_xmls[:3]:
    request_xml.append(raw_page_xml)
    raw_page_xml.set("page_index", str(page_index + 1))

  request_xml = _norm_xml(request_xml)
  resp_xml = llm.request_xml(
    template_name="next_step",
    user_data=request_xml,
    params={},
  )
  xml_string = tostring(resp_xml, encoding="unicode")
  print(xml_string)

def _norm_xml(raw_page_xml: Element) -> Element:
  next_id: int = 1
  for line in _for_each_lines(raw_page_xml):
    line.set("id", str(next_id))
    next_id += 1
  return raw_page_xml

def _for_each_lines(parent: Element) -> Generator[Element[str], None, None]:
  if parent.tag == "line":
    yield parent
  for child in parent:
    yield from _for_each_lines(child)