from dataclasses import dataclass
from typing import Generator
from xml.etree.ElementTree import Element
from ..data import Layout


@dataclass
class Line:
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
  lines: list[Line]
  did_update: bool = False

class Fragment:
  def __init__(self, page_index: int) -> None:
    self._page_index: int = page_index
    self._headlines: list[tuple[Layout, list[Line]]] = []
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
        Line(
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
      to_append_abstract.lines.append(Line(
        id=-1,
        text=text,
        splitted=splitted,
      ))
    else:
      self._abstracts.append(_Abstract(
        raw_layout=parent_layout,
        did_update=False,
        lines=[Line(
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

  def pop_line(self, line_id: int) -> Line | None:
    lines: list[Line] | None = None
    for _, lines in self._headlines:
      for i, line in enumerate(lines):
        if line.id == line_id:
          return lines.pop(i)

    for abstract in self._abstracts:
      for i, line in enumerate(abstract.lines):
        if line.id == line_id and not line.splitted:
          abstract.did_update = True
          return abstract.lines.pop(i)

    return None

  def update_headline(self, id: str, lines: list[Line]):
    for i, (headline, _) in enumerate(self._headlines):
      if headline.id == id:
        self._headlines[i] = (headline, lines)
        return

  def to_request_xml(self, id: int):
    fragment_element = Element("fragment")
    fragment_element.set("id", _to_abc_id(id))
    fragment_element.set("page-index", str(self._page_index))

    for headline, lines in self._headlines:
      headline_element = self._to_headline_xml(headline, lines)
      fragment_element.append(headline_element)

    if self._abstracts:
      abstract_element = Element("abstract")
      fragment_element.append(abstract_element)
      for abstract in self._abstracts:
        for line in abstract.lines:
          abstract_element.append(line.to_xml())

    return fragment_element

  def generate_patch_xmls(self) -> Generator[tuple[int, Element], None, None]:
    for headline, lines in self._headlines:
      headline_element = self._to_headline_xml(headline, lines)
      yield headline.page_index, headline_element

    for abstract in self._abstracts:
      if not abstract.did_update:
        continue
      raw_layout = abstract.raw_layout
      layout_element = Element(raw_layout.kind.value)
      layout_element.set("id", raw_layout.id)
      for line in abstract.lines:
        line_element = Element("line")
        line_element.text = line.text
        layout_element.append(line_element)
      yield raw_layout.page_index, layout_element

  def _lines(self) -> Generator[Line, None, None]:
    for _, lines in self._headlines:
      yield from lines
    for abstract in self._abstracts:
      yield from abstract.lines

  def _to_headline_xml(self, headline: Layout, lines: list[Line]) -> Element:
    headline_xml = Element(headline.kind.value)
    headline_xml.set("id", headline.id)
    for line in lines:
      headline_xml.append(line.to_xml())
    return headline_xml

class FragmentRequest:
  def __init__(self):
    self._fragments: list[Fragment] = []

  @property
  def fragments_count(self) -> int:
    return len(self._fragments)

  @property
  def begin_page_index(self) -> int:
    return min(f.page_index for f in self._fragments)

  @property
  def end_page_index(self) -> int:
    return max(f.page_index for f in self._fragments)

  def append(self, fragment: Fragment) -> None:
    self._fragments.append(fragment)

  def generate_matched_mapper(self, resp_xml: Element) -> Generator[tuple[str, int], None, None]:
    contents_map = resp_xml.find("contents-map")
    if contents_map is None:
      return
    for child in contents_map:
      if child.tag != "match":
        continue
      headline_id = child.get("headline-id", default=None)
      chapter_id = int(child.get("chapter-id", "-1"))
      if headline_id is None or chapter_id < 0:
        continue
      yield headline_id, chapter_id

  def complete_to_xml(self) -> Element:
    next_id: int = 1
    for fragment in self._fragments:
      next_id = fragment.define_line_ids(next_id)

    request_xml = Element("request")
    for id, fragment in enumerate(self._fragments):
      fragment_element = fragment.to_request_xml(id)
      request_xml.append(fragment_element)
    return request_xml

  def generate_patch_xmls(self, resp_xml: Element) -> Generator[tuple[int, Element], None, None]:
    patched_headline_indexes_set: set[int] = set()
    for index, headline_id, line_pairs in self._collect_headline_patch(resp_xml):
      if index >= len(self._fragments):
        continue
      fragment = self._fragments[index]
      lines: list[Line] = []
      for line_id, line_text in line_pairs:
        line = fragment.pop_line(line_id)
        if line is None:
          continue
        line.text = line_text
        lines.append(line)
      fragment.update_headline(headline_id, lines)
      patched_headline_indexes_set.add(index)

    for index in sorted(list(patched_headline_indexes_set)):
      fragment = self._fragments[index]
      yield from fragment.generate_patch_xmls()

  def _collect_headline_patch(self, resp_xml: Element):
    for child in resp_xml:
      if child.tag != "fixed-fragment":
        continue
      index = _parse_abc_id(child.get("id", "A"))
      if index >= len(self._fragments):
        continue
      for headline in child:
        headline_id = headline.get("id", None)
        if headline_id is None:
          continue
        lines: list[tuple[int, str]] = []
        for line in headline:
          if line.tag != "line":
            continue
          line_id = line.get("id", "-1")
          lines.append((int(line_id), line.text))
        yield index, headline_id, lines

def _to_abc_id(id: int) -> str:
  result = ""
  while id > 0:
    id, remainder = divmod(id, 26)
    result = chr(ord("A") + remainder) + result
  if not result:
    result = "A"
  return result

def _parse_abc_id(id: str) -> int:
  result = 0
  for char in id:
    result = result * 26 + (ord(char) - ord("A"))
  return result