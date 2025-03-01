from __future__ import annotations
import os
import io
import re

from dataclasses import dataclass
from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, Element

from .llm import LLM
from .index import Index
from .asset_matcher import ASSET_TAGS
from .utils import read_files, search_xml_children, parse_page_indexes


def serials(llm: LLM, index: Index | None, chunks_path: str) -> Generator[Serial, None, None]:
  yield from _Deduplication(llm, index, chunks_path).for_serials()

@dataclass
class Serial:
  main_texts: list[Element]
  citations: Citations

@dataclass
class Citation:
  id: int
  label: str
  text: Element

class Citations:
  def __init__(self):
    self._refs: dict[int, tuple[int, Citation]] = {}

  def ref(self, citation: Citation) -> Citation:
    id = citation.id
    if id in self._refs:
      count, citation = self._refs[id]
      self._refs[id] = (count + 1, citation)
    else:
      self._refs[id] = (1, citation)
    return citation

  def unref(self, id: int) -> Citation:
    assert id in self._refs, f"Cannot find citation with id {id}"
    count, citation = self._refs[id]
    if count == 1:
      self._refs.pop(id)
    else:
      self._refs[id] = (count - 1, citation)
    return citation

@dataclass
class _Chunk:
  file_name: str
  start_idx: int
  end_idx: int
  index: int
  serial: Serial | None

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
      serial = self._load_serial_and_deduplicate(index, chunk)
      chunk.serial = None
      if serial is not None:
        yield serial

  def _load_serial_and_deduplicate(self, index: int, chunk: _Chunk) -> Serial | None:
    serial = self._load_serial(chunk)
    latest_text = self._find_end_text(serial, False)
    if latest_text is not None:
      duplicated = list(self._find_duplicated_texts_from_serials(latest_text, index))
      if len(duplicated) > 0:
        latest_text_index = serial.main_texts.index(latest_text)
        duplicated.insert(0, (latest_text, serial))
        merged_text, citations = self._remove_and_merge_texts_from_serials(duplicated)
        serial.main_texts.insert(latest_text_index, merged_text)
        # TODO: 将 citations 重新插入

    if len(serial.main_texts) == 0:
      # cleared due to deduplication
      return None

    return serial

  def _find_duplicated_texts_from_serials(self, text: Element, index: int):
    ban_max_index = index - 1 # the processed index cannot be processed again
    search_indexes = [i for i in parse_page_indexes(text) if i != index]
    search_indexes.sort()

    while len(search_indexes) > 0:
      next_index = search_indexes.pop(0)
      serial = self._load_serial(self._chunks[next_index])
      first_text = self._find_end_text(serial, True)
      if first_text is None:
        # If the process breaks down, it means that LLM made an error in judgment
        # and the process must be interrupted (this will not happen under every thing is OK)
        break

      next_indexes = [i for i in parse_page_indexes(first_text) if i != next_index]
      if index not in next_indexes:
        # This means that the index is not in the same order as the current one.
        # Something must have gone wrong. To be on the safe side, end this operation.
        break

      yield first_text, serial

      origin_indexes_count = len(search_indexes)
      for next_index in next_indexes:
        if next_index in search_indexes:
          continue
        if next_index <= ban_max_index:
          continue
        search_indexes.append(next_index)

      if origin_indexes_count != len(search_indexes):
        search_indexes.sort()

      index = next_index
      ban_max_index = max(index)

  def _remove_and_merge_texts_from_serials(self, duplicated: list[tuple[Element, Serial]]):
    citation_matrix: list[dict[int, Citation]] = []
    for text, serial in duplicated:
      citations: dict[int, Citation] = {}
      citation_matrix.append(citations)
      for id in self._search_ref_ids_in_text(text):
        citation = serial.citations.unref(id)
        citations[id] = citation

      index = serial.main_texts.index(text)
      if index > 0:
        serial.main_texts.pop(index)

    index = self._try_to_choose_from_texts(e[0] for e in duplicated)
    if index == -1:
      raise NotImplementedError("TODO: use LLM to choose the best text index")

    text, _ = duplicated[index]
    citations = citation_matrix[index]

    return text, citations

  def _try_to_choose_from_texts(self, texts: Iterable[Element]) -> int:
    str_texts: list[str] = []
    for text in texts:
      buffer = io.StringIO()
      buffer.write(self._normalize_text(text.text))
      for child in text:
        buffer.write("<ref/>")
        buffer.write(self._normalize_text(child.tail))
      str_texts.append(buffer.getvalue())

    no_sub_indexes: list[int] = []
    for i, str_text1 in enumerate(str_texts):
      not_sub = False
      for j, str_text2 in enumerate(str_texts):
        if i != j and str_text1 in str_text2:
          not_sub = True
          break
      if not_sub:
        no_sub_indexes.append(i)

    if len(no_sub_indexes) != 1:
      return -1
    return no_sub_indexes[0]

  def _load_serial(self, chunk: _Chunk):
    if chunk.serial is not None:
      return chunk.serial

    file_path = os.path.join(self._chunks_path, chunk.file_name)
    with open(file_path, "r", encoding="utf-8") as file:
      chunk_xml = fromstring(file.read())

    if self._index is not None:
      content_xml = chunk_xml.find("content")
      self._index.mark_ids_for_headlines(self._llm, content_xml)

    main_texts: list[Element] = []
    for child in content_xml:
      main_texts.append(child)

    citations_xml = chunk_xml.find("citations")
    citations = Citations()

    if citations_xml is not None:
      id = int(citations_xml.get("id"))
      label = citations_xml.find("label").text
      citations.ref(id, Citation(
        id=id,
        label=label,
        text=citations_xml.find("text"),
      ))
    return Serial(main_texts, citations)

  def _find_end_text(self, serial: Serial, is_begin_end: bool) -> Element | None:
    main_texts = serial.main_texts
    range_iter: Iterable[int]

    if is_begin_end:
      range_iter = range(len(main_texts))
    else:
      range_iter = range(len(main_texts) - 1, -1, -1)

    for i in range_iter:
      element = main_texts[i]
      if element.tag == "text":
        return element
      elif element.tag not in ASSET_TAGS:
        # it is normal to insert figures, tables, and formulas and split text
        return None

    return None

  def _search_ref_ids_in_text(self, text: Element):
    for target in search_xml_children(text):
      if target.tag == "ref":
        yield int(target.get("id"))

  def _clean_all_idx_attr(self, element: Element):
    for target in search_xml_children(element):
      target.attrib.pop("idx", None)

  def _normalize_text(self, headline: str) -> str:
    return re.sub(r"\s+", " ", headline).strip()