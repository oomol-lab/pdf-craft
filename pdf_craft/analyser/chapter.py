from __future__ import annotations
from typing import Generator
from dataclasses import dataclass
from xml.etree.ElementTree import Element
from .llm import LLM
from .serial import serials, Citations
from .utils import search_xml_children


def generate_chapters(llm: LLM, chunks_path: str) -> Generator[tuple[int | None, Element], None, None]:
  session: _Session | None = None

  for serial in serials(llm, chunks_path):
    if session is None:
      session = _Session(None, serial.citations)
    else:
      session.update_serial_citations(serial.citations)

    for child in serial.main_texts:
      chapter_id = _try_to_take_chapter_id(child)
      if chapter_id is not None:
        if not session.is_empty:
          yield session.chapter_id, session.to_xml()
        session = _Session(chapter_id, serial.citations)
      session.append(child)

  if session is not None and not session.is_empty:
    yield session.chapter_id, session.to_xml()

def _try_to_take_chapter_id(element: Element) -> int | None:
  if element.tag != "headline":
    return None
  id = element.attrib.pop("id", None)
  if id is None:
    return None
  return int(id)

@dataclass
class _Citation:
  label: str
  content: list[Element]

class _Session:
  def __init__(self, chapter_id: int | None, serial_citations: Citations ):
    self.chapter_id: int | None = chapter_id
    self._elements: list[Element] = []
    self._citations: list[tuple[int, _Citation]] = []
    self._refs: list[tuple[int, Element]] = []
    self._next_ref_id: int = 1
    self._serial_citations: Citations = serial_citations

  @property
  def is_empty(self) -> bool:
    return len(self._elements) == 0

  def append(self, element: Element):
    for child in search_xml_children(element):
      if child.tag == "ref":
        id = int(child.get("id"))
        self._refs.append((id, child))
    self._elements.append(element)

  def update_serial_citations(self, citations: Citations):
    self._reset_ref_ids()
    self._refs.clear()
    self._serial_citations = citations

  def _reset_ref_ids(self):
    ids_map: dict[int, int] = {}
    for origin_id, ref in self._refs:
      citation = self._serial_citations.get(origin_id)
      new_id = ids_map.get(origin_id, None)
      if new_id is None:
        new_id = self._next_ref_id
        self._next_ref_id += 1
        ids_map[origin_id] = new_id
        self._citations.append((new_id, _Citation(
          label=citation.label,
          content=citation.content,
        )))
      ref.set("id", str(new_id))

  def to_xml(self) -> Element:
    self._reset_ref_ids()
    chapter_xml = Element("chapter")
    content_xml = Element("content")
    chapter_xml.append(content_xml)
    for element in self._elements:
      content_xml.append(element)

    if len(self._citations) > 0:
      citations_xml = Element("citations")
      chapter_xml.append(citations_xml)
      for id, citation in self._citations:
        citation_xml = Element("citation")
        citation_xml.set("id", str(id))
        citations_xml.append(citation_xml)
        label_xml = Element("label")
        label_xml.text = citation.label
        citation_xml.append(label_xml)
        for child in citation.content:
          citation_xml.append(child)

    return chapter_xml
