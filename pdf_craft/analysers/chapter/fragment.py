from dataclasses import dataclass
from typing import Generator
from xml.etree.ElementTree import Element
from ..sequence import Layout


@dataclass
class _Line:
  id: int
  text: str
  splitted: bool

  def to_xml(self) -> Element:
    line_xml = Element("line")
    line_xml.text = self.text
    line_xml.set("id", str(self.id))
    return line_xml

@dataclass
class _Abstract:
  raw_layout: Layout
  lines: list[_Line]

class Fragment:
  def __init__(self, page_index: int) -> None:
    self._page_index: int = page_index
    self._headlines: list[tuple[Layout, list[_Line]]] = []
    self._abstracts: list[_Abstract] = []

  @property
  def page_index(self) -> int:
    return self._page_index

  @property
  def is_abstracts_empty(self) -> bool:
    return bool(self._abstracts)

  def append_headline(self, headline_layout: Layout) -> None:
    self._headlines.append((
      headline_layout,
      [
        _Line(
          id=-1,
          text=line.text,
          splitted=False,
        )
        for line in headline_layout.lines
      ],
    ))

  def append_abstract_line(
        self,
        parent_layout: Layout,
        text: str,
        splitted: bool = False,
      ) -> None:

    to_append_abstract: _Abstract | None = None
    if self._abstracts:
      last_abstract = self._abstracts[-1]
      if last_abstract.raw_layout.id == parent_layout.id:
        to_append_abstract = last_abstract

    if to_append_abstract:
      to_append_abstract.lines.append(_Line(
        id=-1,
        text=text,
        splitted=splitted,
      ))
    else:
      self._abstracts.append(_Abstract(
        raw_layout=parent_layout,
        lines=[_Line(
          id=-1,
          text=text,
          splitted=splitted,
        )],
      ))

  def define_line_ids(self, first_id: int) -> int:
    next_id: int = first_id
    for line in self._lines():
      line.id = next_id
      next_id += 1
    return next_id

  def to_xml(self, id: int):
    fragment_element = Element("fragment")
    fragment_element.set("id", _to_abc_id(id))
    fragment_element.set("page-index", str(self._page_index))

    for headline, lines in self._headlines:
      headline_xml = Element(headline.kind.value)
      headline_xml.set("id", headline.id)
      for line in lines:
        headline_xml.append(line.to_xml())
      fragment_element.append(headline_xml)

    if self._abstracts:
      abstract_element = Element("abstract")
      fragment_element.append(abstract_element)
      for abstract in self._abstracts:
        for line in abstract.lines:
          abstract_element.append(line.to_xml())

    return fragment_element

  def _lines(self) -> Generator[_Line, None, None]:
    for _, lines in self._headlines:
      yield from lines
    for abstract in self._abstracts:
      yield from abstract.lines

class FragmentRequest:
  def __init__(self):
    self._fragments: list[Fragment] = []

  @property
  def fragments_count(self) -> int:
    return len(self._fragments)

  def append(self, fragment: Fragment) -> None:
    self._fragments.append(fragment)

  def complete_to_xml(self) -> Element:
    next_id: int = 1
    for fragment in self._fragments:
      next_id = fragment.define_line_ids(next_id)

    request_xml = Element("request")
    for i, fragment in enumerate(self._fragments):
      fragment_element = fragment.to_xml(i+1)
      request_xml.append(fragment_element)
    return request_xml

  def generate_patch(self, resp_xml: Element) -> None:
    pass

def _to_abc_id(id: int) -> str:
  result = ""
  while id > 0:
    id, remainder = divmod(id - 1, 26)
    result = chr(ord("A") + remainder) + result
  assert result, "id must be greater than 0"
  return result