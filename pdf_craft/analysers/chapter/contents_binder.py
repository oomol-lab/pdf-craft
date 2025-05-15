from dataclasses import dataclass
from typing import Generator
from pathlib import Path

from ...llm import LLM
from ..sequence import read_paragraphs, Line, Layout, LayoutKind
from ..contents import Contents

def bind_contents(llm: LLM, content: Contents, sequence_path: Path):
  _ContentsBinder(llm, content, sequence_path).do()

_MAX_ABSTRACT_CONTENT_TOKENS = 150

@dataclass
class _Abstract:
  page_index: int
  headline: list[Line]
  content: list[Line]
  content_tokens: int

class _ContentsBinder:
  def __init__(self, llm: LLM, content: Contents, sequence_path: Path):
    self._llm: LLM = llm
    self._content: Contents = content
    self._sequence_path: Path = sequence_path

  def do(self):
    for abstract in self._read_abstract():
      print("")
      print("#")
      print("\n".join(s.text for s in abstract.headline))
      print("----")
      print("".join(s.text for s in abstract.content))

  def _read_abstract(self) -> Generator[_Abstract, None, None]:
    abstract: _Abstract | None = None
    for layout in self._read_headline_and_text():
      if layout.kind == LayoutKind.HEADLINE:
        if abstract is not None:
          if len(abstract.content) > 0 or \
             abstract.page_index != layout.page_index:
            yield abstract
            abstract = None

        if abstract is None:
          abstract = _Abstract(
            headline=[],
            content=[],
            content_tokens=0,
            page_index=layout.page_index,
          )
        abstract.headline.extend(layout.lines)

      elif abstract is not None and layout.kind == LayoutKind.TEXT:
        for line in layout.lines:
          tokens = self._llm.encode_tokens(line.text)
          next_tokens_count = abstract.content_tokens + len(tokens)
          if next_tokens_count <= _MAX_ABSTRACT_CONTENT_TOKENS:
            abstract.content.append(line)
            abstract.content_tokens = next_tokens_count
          else:
            can_added_tokens_count = next_tokens_count - _MAX_ABSTRACT_CONTENT_TOKENS
            tokens = tokens[:can_added_tokens_count]
            line.text = self._llm.decode_tokens(tokens) + "..."
            abstract.content.append(line)
            abstract.content_tokens = _MAX_ABSTRACT_CONTENT_TOKENS

          if abstract.content_tokens >= _MAX_ABSTRACT_CONTENT_TOKENS:
            yield abstract
            abstract = None
            break

    if abstract is not None:
      yield abstract

  def _read_headline_and_text(self) -> Generator[Layout, None, None]:
    for paragraph in read_paragraphs(self._sequence_path):
      if paragraph.page_index in self._content.page_indexes:
        continue
      for layout in paragraph.layouts:
        if layout.kind not in (LayoutKind.HEADLINE, LayoutKind.TEXT):
          continue
        if all(self._is_empty(line.text) for line in layout.lines):
          continue
        yield layout

  def _is_empty(self, text: str) -> bool:
    for char in text:
      if char not in (" ", "\n", "\r", "\t"):
        return False
    return True