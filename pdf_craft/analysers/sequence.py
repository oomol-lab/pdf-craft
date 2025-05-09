from pathlib import Path
from typing import Iterable, Generator
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

    request_xml = Element("request")
    request_xml.extend(raw_page_xmls[:3])
    resp_xml = self._request_sequences(request_xml)

    for page in self._gen_pages_with_sequences(
      raw_page_xmls=raw_page_xmls[:3],
      resp_xml=resp_xml,
    ):
      print(tostring(page, encoding="unicode"))

  def _request_sequences(self, request_xml: Element) -> Element:
    next_id: int = 1
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

  def _gen_pages_with_sequences(
        self,
        raw_page_xmls: Iterable[Element],
        resp_xml: Element,
      ) -> Generator[Element, None, None]:

    raw_pages: dict[int, Element] = {}
    resp_pages: dict[int, Element] = {}

    for pages, page_xmls in ((raw_pages, raw_page_xmls), (resp_pages, resp_xml)):
      for page_xml in page_xmls:
        try:
          page_index = int(page_xml.get("page-index", None))
          pages[page_index] = page_xml
        except (ValueError, TypeError):
          pass

    for page_index in sorted(list(raw_pages.keys())):
      raw_page = raw_pages[page_index]
      resp_page = resp_pages.get(page_index, None)
      if resp_page is None:
        continue
      yield self._create_page_with_sequences(
        raw_page=raw_page,
        resp_page=resp_page,
      )

  def _create_page_with_sequences(self, raw_page: Element, resp_page: Element) -> Element:
    layout_lines: dict[int, tuple[Element, Element]] = {}
    new_page = Element(
      tag="page",
      attrib=self._pick_attrib(raw_page, ("page-index", "type")),
    )
    for layout in raw_page:
      for line in layout:
        try:
          id: int = int(line.get("id", None))
          layout_lines[id] = (layout, line)
        except (ValueError, TypeError):
          pass

    for group in resp_page:
      sequence = self._create_sequence(
        layout_lines=layout_lines,
        group=group,
        resp_line=group,
      )
      new_page.append(sequence)

    return new_page

  def _create_sequence(
        self,
        layout_lines: dict[int, tuple[Element, Element]],
        group: Element,
        resp_line: Element,
      ) -> Generator[Element, None, None]:

    current_layout: tuple[Element, Element] | None = None
    sequence = Element(
      tag="sequence",
      attrib=self._pick_attrib(
        element=group,
        keys=("type", "truncation-begin", "truncation-end"),
      ),
    )
    for resp_line in group:
      for id in self._iter_line_ids(resp_line):
        result = layout_lines.get(id, None)
        if result is None:
          continue
        layout, line = result
        if current_layout is not None:
          raw_layout, new_layout = current_layout
          if raw_layout != layout:
            sequence.append(new_layout)
            current_layout = None
        if current_layout is None:
          new_layout = Element(
            tag=layout.tag,
            attrib=self._reject_attrib(
              element=layout,
              keys=("indent", "touch-end"),
            ),
          )
          current_layout = (layout, new_layout)
        _, new_layout = current_layout
        new_layout.append(Element(
          tag=line.tag,
          attrib=self._reject_attrib(
            element=line,
            keys=("id",),
          ),
        ))
      if current_layout is not None:
        _, new_layout = current_layout
        sequence.append(new_layout)
        current_layout = None

    return sequence

  def _pick_attrib(self, element: Element, keys: tuple[str, ...]) -> dict[str, str]:
    attr: dict[str, str] = {}
    for key in keys:
      value = element.get(key, None)
      if value is not None:
        attr[key] = value
    return attr

  def _reject_attrib(self, element: Element, keys: tuple[str, ...]) -> dict[str, str]:
    attr: dict[str, str] = {}
    for key, value in element.attrib.items():
      if key not in keys:
        attr[key] = value
    return attr

  def _iter_line_ids(self, line: Element) -> Generator[int, None, None]:
    ids = line.get("ids", None)
    if ids is None:
      return

    ids = ids.split("-")
    id_begin: int
    id_end: int
    try:
      if len(ids) == 1:
        id_begin = int(ids[0])
        id_end = id_begin
      elif len(ids) == 2:
        id_begin = int(ids[0])
        id_end = int(ids[1])
    except ValueError:
      return

    yield from range(id_begin, id_end + 1)