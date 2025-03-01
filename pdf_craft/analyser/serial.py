from __future__ import annotations
import os

from dataclasses import dataclass
from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

from .llm import LLM
from .index import Index
from .asset_matcher import ASSET_TAGS
from .utils import read_files, search_xml_children, parse_page_indexes


@dataclass
class Serial:
  main_texts: list[Element]
  citations: dict[int, Citation]

@dataclass
class Citation:
  id: int
  label: str
  text: Element

def serials(llm: LLM, index: Index | None, chunks_path: str) -> Generator[Serial, None, None]:
  yield from _Deduplication(llm, index, chunks_path).for_serials()

@dataclass
class _Chunk:
  file_name: str
  start_idx: int
  end_idx: int
  index: int
  buffer: Element | None

class _Deduplication:
  def __init__(self, llm: LLM, index: Index | None, chunks_path: str):
    self._llm: LLM = llm
    self._index: Index | None = index
    self._chunks_path: str = chunks_path
    self._chunks: list[_Chunk] = [
      _Chunk(
        file_name=file_name,
        start_idx=index1 - 1,
        end_idx=index2 - 1,
        index=-1,
        buffer=None,
      )
      for file_name, _, index1, index2 in read_files(
        dir_path=chunks_path,
        enable_kinds=("chunk",),
      )
    ]
    self._chunks.sort(key=lambda chunk: chunk.start_idx)
    for i, chunk in enumerate(self._chunks):
      chunk.index = i

  def for_serials(self) -> Generator[Serial, None, None]:
    for index, chunk in enumerate(self._chunks):
      serial = self._load_serial(index, chunk)
      chunk.buffer = None
      if serial is not None:
        yield serial

  def _load_serial(self, index: int, chunk: _Chunk):
    chunk_xml = self._load_xml(chunk)
    content_xml = chunk_xml.find("content")
    latest_text = self._find_end_text(content_xml, False)
    if latest_text is not None:
      duplicated_texts = self._take_duplicated_texts(latest_text, index)
      if len(duplicated_texts) > 0:
        duplicated_texts.insert(0, latest_text)
        merged_text = self._merge_texts(duplicated_texts)
        if latest_text != merged_text:
          self._replace_child(content_xml, latest_text, merged_text)
          # TODO: 并非替换这么简单，citations 要怎么办？也要映射到正确的位置。

    main_texts: list[Element] = []
    for child in content_xml:
      main_texts.append(child)

    if len(main_texts) == 0:
      # cleared due to deduplication
      return

    citations_xml = chunk_xml.find("citations")
    citations: dict[int, Citation] = {}

    if citations_xml is not None:
      id = int(citations_xml.get("id"))
      label = citations_xml.find("label").text
      citations[id] = Citation(
        id=id,
        label=label,
        text=citations_xml.find("text"),
      )

    return Serial(main_texts, citations)

  def _take_duplicated_texts(self, text: Element, index: int) -> list[Element]:
    duplicated_texts: list[Element] = []
    search_indexes = [i for i in parse_page_indexes(text) if i != index]
    search_indexes.sort()

    while len(search_indexes) > 0:
      next_index = search_indexes.pop(0)
      next_chunk_xml = self._load_xml(self._chunks[next_index])
      next_content_xml = next_chunk_xml.find("content")
      first_text = self._find_end_text(next_content_xml, True)
      if first_text is not None:
        # If the process breaks down, it means that LLM made an error in judgment
        # and the process must be interrupted (this will not happen under every thing is OK)
        break

      next_indexes = [i for i in parse_page_indexes(first_text) if i != next_index]
      if index not in next_indexes:
        # This means that the index is not in the same order as the current one.
        # Something must have gone wrong. To be on the safe side, end this operation.
        break

      did_update = False
      index = next_index
      duplicated_texts.append(first_text)

      for next_index in next_indexes:
        if next_index not in search_indexes:
          search_indexes.append(next_index)
          did_update = True
      if did_update:
        search_indexes.sort()

    return duplicated_texts

  def _merge_texts(self, texts: list[Element]) -> Element:
    # TODO: Implement this method
    raise NotImplementedError()

  def _find_end_text(self, content_xml: Element, is_begin_end: bool) -> Element | None:
    range_iter: Iterable[int]
    if is_begin_end:
      range_iter = range(len(content_xml))
    else:
      range_iter = range(len(content_xml) - 1, -1, -1)

    for i in range_iter:
      element = content_xml[i]
      if element.tag == "text":
        return element
      elif element.tag not in ASSET_TAGS:
        # it is normal to insert figures, tables, and formulas and split text
        return None

    return None

  def _load_xml(self, chunk: _Chunk):
    if chunk.buffer is None:
      file_path = os.path.join(self._chunks_path, chunk.file_name)
      with open(file_path, "r", encoding="utf-8") as file:
        chunk_xml = fromstring(file.read())
      chunk.buffer = chunk_xml
      if self._index is not None:
        content_xml = chunk_xml.find("content")
        self._index.mark_ids_for_headlines(self._llm, content_xml)
    return chunk.buffer

  def _clean_all_idx_attr(self, element: Element):
    for target in search_xml_children(element):
      target.attrib.pop("idx", None)

  def _replace_child(self, parent: Element, old_child: Element, new_child: Element):
    index: int = -1
    for i, child in enumerate(parent):
      if child == old_child:
        index = i
        break
    if index == -1:
      parent.remove(old_child)
      parent.insert(index, new_child)