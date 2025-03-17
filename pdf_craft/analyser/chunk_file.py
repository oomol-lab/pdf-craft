from __future__ import annotations

import re
import os
import sys

from typing import Generator, Iterable
from xml.etree.ElementTree import fromstring, tostring, Element
from resource_segmentation import Resource, Group, Segment
from .common import PageRef


class ChunkFile:
  def __init__(self, output_dir_path: str):
    self._output_dir_path: str = output_dir_path
    self._files: list[tuple[int, int, str]] = list(self._search_chunk_file(output_dir_path))
    self._files.sort(key=lambda x: (x[0], x[1]))
    self._overlapped_files: dict[tuple[int, int], str] = {}
    self._min_page_index: int = sys.maxsize
    self._max_page_index: int = 0

  def __enter__(self) -> ChunkFile:
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_type is not None:
      return
    for file_name in self._overlapped_files.values():
      self._remove_file(file_name)
    for start, end, file_name in self._files:
      if start > self._max_page_index:
        self._remove_file(file_name)
      if end < self._min_page_index:
        self._remove_file(file_name)

  def filter_origin_files(self, origin_dir_path: str) -> Generator[tuple[int, int, Element], None, None]:
    for start, end, file_name in self._search_chunk_file(origin_dir_path):
      self._register_page_range(start, end)
      if self._overlap_files(start, end):
        file_path = os.path.join(origin_dir_path, file_name)
        with open(file_path, "r", encoding="utf-8") as file:
          yield start, end, fromstring(file.read())

  def _search_chunk_file(self, dir_path: str) -> Generator[tuple[int, int, str], None, None]:
    for file_name in os.listdir(dir_path):
      matches = re.match(r"^chunk_(\d+_\d+)\.xml$", file_name)
      if not matches:
        continue
      start, end = matches.group(1).split("_")
      yield int(start) - 1, int(end) - 1, file_name

  def filter_groups(self, groups: Iterable[Group[PageRef]]) -> Generator[tuple[int, int, Group], None, None]:
    for group in groups:
      start = min(t.payload.page_index for t in self._search_text_infos(group))
      end = max(t.payload.page_index for t in self._search_text_infos(group))
      self._register_page_range(start, end)

      if self._overlap_files(start, end):
        yield start, end, group

  def _search_text_infos(self, group: Group[PageRef]):
    for item in group.body:
      if isinstance(item, Segment):
        for text_info in item.resources:
          yield text_info
      elif isinstance(item, Resource):
        yield item

  def _register_page_range(self, start: int, end: int):
    self._min_page_index = min(self._min_page_index, start)
    self._max_page_index = max(self._max_page_index, end)

  # return False if matches origin chunk file
  def _overlap_files(self, start: int, end: int):
    overlap_files = list(self._search_overlap_files(start, end))
    overlap_files.sort(key=lambda x: (x[0], x[1]))

    if len(overlap_files) == 1:
      overlap_start, overlap_end, _ = overlap_files[0]
      if start == overlap_start and end == overlap_end:
        return False

    if len(overlap_files) > 0:
      self._overlapped_files[(start, end)] = overlap_files

    return True

  def _search_overlap_files(self, start: int, end: int):
    for file_start, file_end, file_name in self._files:
      if end < file_start:
        continue
      if start > file_end:
        continue
      yield file_start, file_end, file_name

  def atomic_write_chunk(self, start: int, end: int, chunk_xml: Element):
    overlap_files = self._overlapped_files.pop((start, end), None)
    if overlap_files is not None:
      for _, _, file_name in overlap_files:
        self._remove_file(file_name)

    file_name = f"chunk_{start + 1}_{end + 1}.xml"
    file_path = os.path.join(self._output_dir_path, file_name)
    try:
      with open(file_path, "w", encoding="utf-8") as file:
        content = tostring(chunk_xml, encoding="unicode")
        file.write(content)
        file.flush()
    except Exception as e:
      if os.path.exists(file_path):
        os.unlink(file_path)
      raise e

  def _remove_file(self, file_name: str):
    file_path = os.path.join(self._output_dir_path, file_name)
    if os.path.exists(file_path):
      os.remove(file_path)