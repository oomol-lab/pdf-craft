from typing import Generator
from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..sequence import read_paragraphs, Layout, LayoutKind
from ..contents import Contents, Chapter
from .fragment import Fragment, FragmentRequest


def map_contents(llm: LLM, content: Contents, sequence_path: Path, max_request_tokens: int):
  _ContentsMapper(llm, content, sequence_path, max_request_tokens).do()

_MAX_ABSTRACT_CONTENT_TOKENS = 150

class _ContentsMapper:
  def __init__(self, llm: LLM, content: Contents, sequence_path: Path, max_request_tokens: int):
    self._llm: LLM = llm
    self._contents: Contents = content
    self._sequence_path: Path = sequence_path
    self._max_request_tokens: int = max_request_tokens

  def do(self):
    contents_tokens_count = self._llm.count_tokens_count(
      text=encode_friendly(self._get_contents_xml()),
    )
    for request in self._gen_request(contents_tokens_count):
      request_xml = request.complete_to_xml()
      request_xml.insert(0, self._get_contents_xml())
      resp_xml = self._llm.request_xml(
        template_name="contents/mapper",
        user_data=request_xml,
        params={
          "fragments_count": request.fragments_count,
        },
      )
      request.generate_patch(resp_xml)

  def _gen_request(self, contents_tokens_count: int) -> Generator[FragmentRequest, None, None]:
    request = FragmentRequest()
    request_tokens = 0
    max_request_tokens = max(
      self._max_request_tokens - contents_tokens_count,
      int(self._max_request_tokens * 0.25),
    )
    for fragment in self._read_fragment():
      id = 1 # only for calculate tokens. won't be used in request
      request_text = encode_friendly(fragment.to_xml(id))
      tokens = len(self._llm.encode_tokens(request_text))
      if request_tokens > 0 and request_tokens + tokens > max_request_tokens:
        yield request
        request = FragmentRequest()
        request_tokens = 0

      request_tokens += tokens
      request.append(fragment)

    if request_tokens > 0:
      yield request

  def _read_fragment(self) -> Generator[Fragment, None, None]:
    fragment: Fragment | None = None
    fragment_tokens_count: int = 0

    for layout in self._read_headline_and_text():
      if layout.kind == LayoutKind.HEADLINE:
        if fragment is not None:
          if fragment.is_abstracts_empty or \
             fragment.page_index != layout.page_index:
            yield fragment
            fragment = None

        if fragment is None:
          fragment = Fragment(layout.page_index)
        fragment.append_headline(layout)

      elif fragment is not None and layout.kind == LayoutKind.TEXT:
        for line in layout.lines:
          tokens = self._llm.encode_tokens(line.text)
          next_tokens_count = fragment_tokens_count + len(tokens)
          if next_tokens_count <= _MAX_ABSTRACT_CONTENT_TOKENS:
            fragment.append_abstract_line(
              parent_layout=layout,
              text=line.text,
            )
            fragment_tokens_count += len(tokens)
          else:
            can_added_tokens_count = next_tokens_count - _MAX_ABSTRACT_CONTENT_TOKENS
            tokens = tokens[:can_added_tokens_count]
            fragment.append_abstract_line(
              parent_layout=layout,
              text=self._llm.decode_tokens(tokens) + "...",
              splitted=True,
            )
            fragment_tokens_count = _MAX_ABSTRACT_CONTENT_TOKENS

          if fragment_tokens_count >= _MAX_ABSTRACT_CONTENT_TOKENS:
            yield fragment
            fragment = None
            fragment_tokens_count = 0
            break

    if fragment is not None:
      yield fragment

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

  def _is_empty(self, text: str) -> bool:
    for char in text:
      if char not in (" ", "\n", "\r", "\t"):
        return False
    return True