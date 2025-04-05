from __future__ import annotations
import os
import io

from dataclasses import dataclass
from typing import Iterable, Generator
from xml.etree.ElementTree import fromstring, tostring, Element

from ..llm import LLM
from .asset_matcher import ASSET_TAGS
from .utils import read_files, normalize_xml_text, search_xml_children, parse_page_indexes


def serials(llm: LLM, chunks_path: str) -> Generator[Serial, None, None]:
  yield from _Deduplication(llm, chunks_path).for_serials()

@dataclass
class Serial:
  main_texts: list[Element]
  citations: Citations

@dataclass
class Citation:
  id: int
  label: str
  content: list[Element]
  str_text: str

class Citations:
  def __init__(self):
    self._refs: dict[int, tuple[int, Citation]] = {}
    self._max_id: int = 0

  def __iter__(self) -> Generator[Citation, None, None]:
    for _, citation in self._refs.values():
      yield citation

  def get(self, id: int) -> Citation | None:
    ref = self._refs.get(id, None)
    if ref is None:
      return None
    return ref[1]

  # deduplication: will return citation with different id if the citation is already in the list
  def ref(self, id: int, label: str, content: list[Element]) -> Citation:
    str_text = "".join(tostring(e, encoding="unicode") for e in content)
    for count, citation in self._refs.values():
      if citation.label != label or citation.str_text != str_text:
        continue
      self._refs[citation.id] = (count + 1, citation)
      return citation

    if id in self._refs:
      id = self._max_id + 1
    citation = Citation(id, label, content, str_text)
    self._refs[id] = (1, citation)
    self._max_id = max(self._max_id, id)

    return citation

  def unref(self, id: int) -> Citation:
    assert id in self._refs, f"Cannot find citation with id {id}"
    count, citation = self._refs[id]
    if count == 1:
      self._refs.pop(id, None)
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
  def __init__(self, llm: LLM, chunks_path: str):
    self._llm: LLM = llm
    self._chunks_path: str = chunks_path
    self._chunks: list[_Chunk] = [
      _Chunk(
        file_name=file_name,
        start_idx=index1 - 1,
        end_idx=index2 - 1,
        index=-1,
        serial=None,
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
        yield self._clean_all_idx_attr_with_serial(serial)

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

        for id, ref in self._search_refs_in_text(merged_text):
          citation = citations[id]
          deduplicated_citation = serial.citations.ref(
            id=id,
            label=citation.label,
            content=citation.content,
          )
          if id != deduplicated_citation.id:
            ref.set("id", str(deduplicated_citation.id))

    if len(serial.main_texts) == 0:
      # cleared due to deduplication
      return None
    else:
      return serial

  def _find_duplicated_texts_from_serials(self, text: Element, index: int):
    ban_max_index = index # the processed index cannot be processed again
    search_indexes = self._chunk_indexes_with_text(text, index)

    while len(search_indexes) > 0:
      next_index = search_indexes.pop(0)
      serial = self._load_serial(self._chunks[next_index])
      first_text = self._find_end_text(serial, True)
      if first_text is None:
        # If the process breaks down, it means that LLM made an error in judgment
        # and the process must be interrupted (this will not happen under every thing is OK)
        break

      next_indexes = self._chunk_indexes_with_text(first_text, next_index)
      if index not in next_indexes:
        # This means that the index is not in the same order as the current one.
        # Something must have gone wrong. To be on the safe side, end this operation.
        break

      yield first_text, serial

      origin_indexes_count = len(search_indexes)
      for i in next_indexes:
        if i in search_indexes:
          continue
        if i <= ban_max_index:
          continue
        search_indexes.append(i)

      if origin_indexes_count != len(search_indexes):
        search_indexes.sort()

      index = next_index
      ban_max_index = max(index, ban_max_index)

  def _remove_and_merge_texts_from_serials(self, duplicated: list[tuple[Element, Serial]]):
    citation_matrix: list[dict[int, Citation]] = []
    for text, serial in duplicated:
      citations: dict[int, Citation] = {}
      citation_matrix.append(citations)
      for id, _ in self._search_refs_in_text(text):
        citation = serial.citations.unref(id)
        citations[id] = citation
      serial.main_texts.remove(text)

    index = self._try_to_choose_from_texts(e[0] for e in duplicated)
    text, _ = duplicated[index]
    citations = citation_matrix[index]

    return text, citations

  def _try_to_choose_from_texts(self, texts: Iterable[Element]) -> int:
    str_texts: list[str] = []
    for text in texts:
      buffer = io.StringIO()
      buffer.write(normalize_xml_text(text.text))
      for child in text:
        buffer.write("<ref/>")
        if child.tail is not None:
          buffer.write(normalize_xml_text(child.tail))
      str_texts.append(buffer.getvalue())

    no_sub_indexes: list[int] = []
    for i, str_text1 in enumerate(str_texts):
      not_sub = True
      for j, str_text2 in enumerate(str_texts):
        if i != j and str_text1 in str_text2:
          not_sub = False
          break
      if not_sub:
        no_sub_indexes.append(i)

    if len(no_sub_indexes) != 1:
      # TODO: use LLM to choose the best text index
      max_len: int = 0
      max_len_index: int = -1
      for i, str_text in enumerate(str_texts):
        text_len = len(str_text)
        if text_len > max_len:
          max_len = text_len
          max_len_index = i
      return max_len_index

    return no_sub_indexes[0]

  def _load_serial(self, chunk: _Chunk):
    if chunk.serial is not None:
      return chunk.serial

    file_path = os.path.join(self._chunks_path, chunk.file_name)
    with open(file_path, "r", encoding="utf-8") as file:
      chunk_xml = fromstring(file.read())

    content_xml = chunk_xml.find("content")
    main_texts: list[Element] = []
    for child in content_xml:
      main_texts.append(child)

    citations_xml = chunk_xml.find("citations")
    citations = Citations()

    if citations_xml is not None:
      for citation_xml in citations_xml:
        id = int(citation_xml.get("id"))
        label_xml: Element | None = None
        content: list[Element] = []
        for child in citation_xml:
          if child.tag == "label":
            label_xml = child
          else:
            content.append(child)

        assert label_xml is not None, "Citation must have a label"
        citation = citations.ref(
          id=id,
          label=label_xml.text,
          content=content,
        )
        if citation.id != id: # be deduplicated
          citation_xml.set("id", str(citation.id))

    serial = Serial(main_texts, citations)
    chunk.serial = serial

    return serial

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

  def _search_refs_in_text(self, text: Element):
    for target, _ in search_xml_children(text):
      if target.tag == "ref":
        id = int(target.get("id"))
        yield id, target

  def _clean_all_idx_attr_with_serial(self, serial: Serial):
    for child in serial.main_texts:
      self._clean_all_idx_attr(child)
    for citation in serial.citations:
      for child in citation.content:
        self._clean_all_idx_attr(child)
    return serial

  def _chunk_indexes_with_text(self, text: Element, expected_chunk_index: int):
    chunk_indexes: list[int] = []
    for page_index in parse_page_indexes(text):
      for chunk_index, chunk in enumerate(self._chunks):
        if chunk_index != expected_chunk_index and \
           chunk_index not in chunk_indexes and \
           chunk.start_idx <= page_index <= chunk.end_idx:
          chunk_indexes.append(chunk_index)
    chunk_indexes.sort()
    return chunk_indexes

  def _clean_all_idx_attr(self, element: Element):
    for target, _ in search_xml_children(element):
      target.attrib.pop("idx", None)
    element.attrib.pop("idx", None)
