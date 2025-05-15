from dataclasses import dataclass
from typing import Generator
from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..sequence import read_paragraphs, Line, Layout, LayoutKind
from ..contents import Contents, Chapter

def bind_contents(llm: LLM, content: Contents, sequence_path: Path, max_request_tokens: int):
  _ContentsBinder(llm, content, sequence_path, max_request_tokens).do()

_MAX_ABSTRACT_CONTENT_TOKENS = 150

@dataclass
class _Abstract:
  page_index: int
  headlines: list[Layout]
  contents: list[Line]
  content_tokens: int

class _ContentsBinder:
  def __init__(self, llm: LLM, content: Contents, sequence_path: Path, max_request_tokens: int):
    self._llm: LLM = llm
    self._contents: Contents = content
    self._sequence_path: Path = sequence_path
    self._max_request_tokens: int = max_request_tokens

  def do(self):
    contents_tokens_count = self._llm.count_tokens_count(
      text=encode_friendly(self._get_contents_xml()),
    )
    for request_xml, abstracts in self._gen_requests(contents_tokens_count):
      request_xml.insert(0, self._get_contents_xml())
      self._llm.request_xml(
        template_name="contents/binder",
        user_data=request_xml,
        params={
          "fragments_count": len(abstracts),
        },
      )

  def _gen_requests(self, contents_tokens_count: int) -> Generator[tuple[Element, list[_Abstract]], None, None]:
    request_xml = Element("request")
    request_tokens = 0
    abstracts: list[_Abstract] = []
    max_request_tokens = max(
      self._max_request_tokens - contents_tokens_count,
      int(self._max_request_tokens * 0.25),
    )
    for abstract in self._read_abstract():
      fragment = self._to_fragment_xml(abstract)
      tokens = len(self._llm.encode_tokens(encode_friendly(fragment)))
      if request_tokens > 0 and request_tokens + tokens > max_request_tokens:
        yield request_xml, abstracts
        request_xml = Element("request")
        request_tokens = 0
        abstracts = []

      request_tokens += tokens
      request_xml.append(fragment)
      abstracts.append(abstract)

    if request_tokens > 0:
      yield request_xml, abstracts

  def _read_abstract(self) -> Generator[_Abstract, None, None]:
    abstract: _Abstract | None = None
    for layout in self._read_headline_and_text():
      if layout.kind == LayoutKind.HEADLINE:
        if abstract is not None:
          if len(abstract.contents) > 0 or \
             abstract.page_index != layout.page_index:
            yield abstract
            abstract = None

        if abstract is None:
          abstract = _Abstract(
            headlines=[],
            contents=[],
            content_tokens=0,
            page_index=layout.page_index,
          )
        abstract.headlines.append(layout)

      elif abstract is not None and layout.kind == LayoutKind.TEXT:
        for line in layout.lines:
          tokens = self._llm.encode_tokens(line.text)
          next_tokens_count = abstract.content_tokens + len(tokens)
          if next_tokens_count <= _MAX_ABSTRACT_CONTENT_TOKENS:
            abstract.contents.append(line)
            abstract.content_tokens = next_tokens_count
          else:
            can_added_tokens_count = next_tokens_count - _MAX_ABSTRACT_CONTENT_TOKENS
            tokens = tokens[:can_added_tokens_count]
            line.text = self._llm.decode_tokens(tokens) + "..."
            abstract.contents.append(line)
            abstract.content_tokens = _MAX_ABSTRACT_CONTENT_TOKENS

          if abstract.content_tokens >= _MAX_ABSTRACT_CONTENT_TOKENS:
            yield abstract
            abstract = None
            break

    if abstract is not None:
      yield abstract

  def _read_headline_and_text(self) -> Generator[Layout, None, None]:
    for paragraph in read_paragraphs(self._sequence_path):
      if paragraph.page_index in self._contents.page_indexes:
        continue
      for layout in paragraph.layouts:
        if layout.kind not in (LayoutKind.HEADLINE, LayoutKind.TEXT):
          continue
        if all(self._is_empty(line.text) for line in layout.lines):
          continue
        yield layout

  def _get_contents_xml(self) -> Element:
    contents_element = Element("contents")
    for tag, chapters in (
      ("prefaces", self._contents.prefaces),
      ("chapters", self._contents.chapters),
    ):
      if chapters:
        chapters_element = Element(tag)
        contents_element.append(chapters_element)
        for chapter in chapters:
          chapter_element = self._to_chapter_xml(chapter)
          chapters_element.append(chapter_element)
    return contents_element

  def _to_chapter_xml(self, chapter: Chapter) -> Element:
    chapter_element = Element("chapter")
    chapter_element.set("id", str(chapter.id))
    chapter_element.text = chapter.name
    for child in chapter.children:
      child_xml = self._to_chapter_xml(child)
      chapter_element.append(child_xml)
    return chapter_element

  def _to_fragment_xml(self, abstract: _Abstract):
    fragment_element = Element("fragment")
    fragment_element.set("page-index", str(abstract.page_index))
    for headline in abstract.headlines:
      fragment_element.append(headline.xml())

    if abstract.contents:
      abstract_element = Element("abstract")
      fragment_element.append(abstract_element)
      for line in abstract.contents:
        abstract_element.append(line.xml())

    return fragment_element

  def _is_empty(self, text: str) -> bool:
    for char in text:
      if char not in (" ", "\n", "\r", "\t"):
        return False
    return True