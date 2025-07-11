import sys

from pathlib import Path
from typing import Iterable, Generator
from xml.etree.ElementTree import Element

from .common import State, Phase, SequenceType, Truncation
from .request import SequenceRequest, RawPage
from ...llm import LLM
from ...xml import encode
from ..data import ASSET_LAYOUT_KINDS
from ..utils import (
  remove_file,
  read_xml_file,
  xml_files,
  search_xml_children,
  Context,
  MultiThreads,
  Partition,
  PartitionTask,
)


def extract_ocr(llm: LLM, context: Context[State], ocr_path: Path, threads: MultiThreads) -> None:
  sequence = _Sequence(llm, context, threads)
  return sequence.to_sequences(ocr_path)

_LayoutLines = dict[int, tuple[Element, Element]]

class _Sequence:
  def __init__(self, llm: LLM, context: Context[State], threads: MultiThreads) -> None:
    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._threads: MultiThreads = threads

  def to_sequences(self, ocr_path: Path):
    save_path = self._ctx.path.joinpath(Phase.EXTRACTION.value)
    save_path.mkdir(parents=True, exist_ok=True)

    self._ctx.reporter.set(max_count=len(xml_files(ocr_path)))
    def _add_progress_by_pages(begin, end):
      page_count = end[0] - begin[0] + 1
      self._ctx.reporter.increment(page_count)

    partition: Partition[tuple[int], State, SequenceRequest] = Partition(
      dimension=1,
      context=self._ctx,
      sequence=((r.begin, r.end, r) for r in self._split_requests(ocr_path)),
      done=_add_progress_by_pages,
      remove=lambda begin, end: remove_file(save_path / f"pages_{begin[0]}_{end[0]}.xml"),
    )
    with partition:
      self._threads.run(
        next_task=partition.pop_task,
        thread_payload=lambda: None,
        invoke=lambda _, task: self._emit_request(save_path, task),
      )

  def _split_requests(self, ocr_path: Path) -> Generator[SequenceRequest, None, None]:
    max_request_data_tokens = self._ctx.state["max_request_data_tokens"]
    request = SequenceRequest()
    request_tokens: int = 0

    for xml_path, _, page_index, _ in xml_files(ocr_path):
      raw_page = RawPage(
        raw_element=read_xml_file(xml_path),
        page_index=page_index,
      )
      if not raw_page.children: # empty page
        continue

      tokens = raw_page.tokens_count(self._llm)
      if request_tokens > 0 and request_tokens + tokens > max_request_data_tokens:
        yield request
        request = SequenceRequest()

      request.append(page_index, raw_page)
      request_tokens += tokens

    if request_tokens > 0:
      yield request

  def _emit_request(self, save_path: Path, task: PartitionTask[tuple[int], State, SequenceRequest]):
    with task:
      begin = task.begin[0]
      end = task.end[0]
      request = task.payload
      request_xml = request.inject_ids_and_get_xml()
      resp_xml = self._request_sequences(request_xml)
      data_xml = Element("pages")
      data_xml.set("begin-page-index", str(begin))
      data_xml.set("end-page-index", str(end))

      for page in self._gen_pages_with_sequences(
        request=request,
        raw_page_xmls=request_xml,
        resp_xml=resp_xml,
      ):
        data_xml.append(page)

      data_file_path = save_path / f"pages_{begin}_{end}.xml"
      with open(data_file_path, mode="w", encoding="utf-8") as file:
        file.write(encode(data_xml))

  def _request_sequences(self, request_xml: Element) -> Element:
    next_id: int = 1
    for page in request_xml:
      for layout in page:
        for child, _ in search_xml_children(layout):
          child.set("id", str(next_id))
          next_id += 1

    return self._llm.request_xml(
      template_name="sequence/extraction",
      user_data=request_xml,
    )

  def _gen_pages_with_sequences(
        self,
        request: SequenceRequest,
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
        request=request,
        page_index=page_index,
        raw_page_element=raw_page,
        resp_page=resp_page,
      )

  def _create_page_with_sequences(
        self,
        request: SequenceRequest,
        page_index: int,
        raw_page_element: Element,
        resp_page: Element,
      ) -> Element:

    layout_lines: _LayoutLines = {}
    new_page = Element(
      "page",
      self._pick_attrib(resp_page, ("page-index", "type")),
    )
    for layout in raw_page_element:
      for line in layout:
        try:
          id: int = int(line.get("id", None))
          layout_lines[id] = (layout, line)
        except (ValueError, TypeError):
          pass

    text, footnote = self._text_and_footnote_groups(resp_page, layout_lines)
    begin: tuple[int, int, Element] | None = None
    end: tuple[int, int, Element] | None = None
    raw_page = request.raw_page(page_index)

    for group_pair in (text, footnote):
      if group_pair is None:
        continue
      group, ids = group_pair
      list_ids = list(set(ids))
      sequence = self._create_sequence(
        layout_lines=layout_lines,
        group=group,
        group_ids=list_ids,
        raw_page=raw_page,
      )
      new_page.append(sequence)
      end = self._term_sequence(sequence, list_ids)
      if begin is None:
        begin = self._term_sequence(sequence, list_ids)

    if raw_page is not None:
      if begin:
        begin_line_id, _, sequence = begin
        for element in reversed(list(raw_page.assets_in_range(
          before_line_id=begin_line_id,
        ))):
          sequence.insert(0, element)

      if begin and end:
        _, after_line_id, _ = begin
        begin_line_id, _, _ = end
        if after_line_id < begin_line_id:
          sequence.extend(raw_page.assets_in_range(
            after_line_id=after_line_id,
            before_line_id=begin_line_id,
          ))

      if end:
        _, end_line_id, sequence = end
        for element in raw_page.assets_in_range(
          after_line_id=end_line_id,
        ):
          sequence.append(element)

    for child, _ in search_xml_children(new_page):
      if child.tag == "line":
        child.attrib.pop("id", None)

    return new_page

  def _text_and_footnote_groups(self, resp_page: Element, layout_lines: _LayoutLines):
    text: tuple[Element, list[int]] | None = None
    footnote: tuple[Element, list[int]] | None = None
    origin_text_ids: set[int] = set()
    origin_footnote_ids: set[int] = set()

    for group in resp_page:
      type = group.get("type", None)
      if type == SequenceType.TEXT:
        text_ids = self._ids_from_group(group)
        if text_ids:
          text = (group, text_ids)
          origin_text_ids.update(text_ids)
      elif type == SequenceType.FOOTNOTE:
        footnote_ids = self._ids_from_group(group)
        if footnote_ids:
          footnote = (group, footnote_ids)
          origin_footnote_ids.update(footnote_ids)

    if text and footnote:
      text_group, text_ids = text
      footnote_group, footnote_ids = footnote

      # There may be overlap between the two.
      # In this case, the footnote header should be used as the reference for re-cutting.
      if text_ids[-1] >= footnote_ids[0]:
        text_id_cut_index: int = -1
        for i, id in enumerate(text_ids):
          if id >= footnote_ids[0]:
            text_id_cut_index = i
            break
        text = (text_group, text_ids[:text_id_cut_index])
        footnote_ids = sorted(footnote_ids + text_ids[text_id_cut_index:])
        footnote = (footnote_group, footnote_ids)
        if footnote_ids[-1] not in origin_footnote_ids:
          footnote_group.set(
            "truncation-end",
            text_group.get("truncation-end", None),
          )
          origin_footnote_ids = set(footnote_ids)

    if text:
      text_group, text_ids = text
      before_id: int | None = None
      if footnote:
        _, footnote_ids = footnote
        if footnote_ids:
          before_id = footnote_ids[0]

      text_ids = self._fill_gap_with_lines(
        raw_ids=text_ids,
        layout_lines=layout_lines,
        before_id=before_id,
        ignore_origin_abandon=True,
      )
      if text_ids:
        text = (text_group, text_ids)
        if text_ids[-1] not in origin_text_ids:
          text_group.set("truncation-end", Truncation.UNCERTAIN.value)
      else:
        text = None

    if footnote:
      footnote_group, footnote_ids = footnote
      footnote_ids = text_ids = self._fill_gap_with_lines(
        raw_ids=footnote_ids,
        layout_lines=layout_lines,
        before_id=None,
        ignore_origin_abandon=False,
      )
      if footnote_ids:
        footnote = (footnote_group, footnote_ids)
        if footnote_ids[-1] not in origin_footnote_ids:
          footnote_group.set("truncation-end", Truncation.UNCERTAIN.value)
      else:
        footnote = None

    return text, footnote

  def _ids_from_group(self, group: Element) -> list[int]:
    ids: list[int] = []
    for resp_line in group:
      for id in self._iter_line_ids(resp_line):
        ids.append(id)
    return sorted(ids)

  def _fill_gap_with_lines(
        self,
        raw_ids: list[int],
        layout_lines: _LayoutLines,
        before_id: int | None,
        ignore_origin_abandon: bool,
      ) -> list[int]:

    target_ids: list[int] = []
    if not raw_ids:
      return target_ids

    def check_and_collect(gap_id: int):
      gap = layout_lines.get(gap_id, None)
      if gap is None:
        return
      layout, _ = gap
      if layout.tag == "abandon" or layout.tag in ASSET_LAYOUT_KINDS:
        return
      target_ids.append(gap_id)

    pre_id = raw_ids[0] - 1
    for id in raw_ids:
      for gap_id in range(pre_id + 1, id):
        check_and_collect(gap_id)

      pre_id = id
      origin = layout_lines.get(id, None)
      if origin is not None:
        layout, _ = origin
        if not ignore_origin_abandon or layout.tag != "abandon":
          target_ids.append(id)

    if before_id is not None:
      for gap_id in range(pre_id + 1, before_id):
        check_and_collect(gap_id)

    return target_ids

  def _term_sequence(self, sequence: Element, ids: Iterable[int]) -> tuple[int, int, Element]:
    begin_id: int = sys.maxsize
    end_id: int = -1
    for id in ids:
      if id < begin_id:
        begin_id = id
      if id > end_id:
        end_id = id
    return begin_id, end_id, sequence

  def _create_sequence(
        self,
        layout_lines: _LayoutLines,
        group: Element,
        group_ids: list[int],
        raw_page: RawPage | None,
      ) -> Generator[Element, None, None]:

    # TODO: 由于 asset 的插入，会导致“断裂分析”结果不一定准确，此处需要重新设计
    group_id_and_asset_element: Iterable[tuple[int, Element | None]] = ()
    if raw_page is not None:
      group_id_and_asset_element = raw_page.inject_assets(group_ids)
    else:
      group_id_and_asset_element = ((id, None) for id in sorted(group_ids))

    current_layout: tuple[Element, Element] | None = None
    sequence = Element(
      "sequence",
      self._pick_attrib(
        element=group,
        keys=("type", "truncation-begin", "truncation-end"),
      ),
    )
    for id, asset_layout in group_id_and_asset_element:
      if asset_layout is not None:
        sequence.append(asset_layout)
        continue

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
          layout.tag,
          self._reject_attrib(
            element=layout,
            keys=("indent", "touch-end"),
          ),
        )
        current_layout = (layout, new_layout)

      _, new_layout = current_layout
      new_line = Element(line.tag, line.attrib)
      new_line.text = line.text
      new_line.tail = line.tail
      new_layout.append(new_line)

    if current_layout is not None:
      _, new_layout = current_layout
      sequence.append(new_layout)

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
    ids = line.get("id", None)
    if ids is None:
      return

    ids = ids.split("-")
    id_begin: int
    id_end: int

    if len(ids) == 1:
      id_begin = int(ids[0])
      id_end = id_begin
    elif len(ids) == 2:
      id_begin = int(ids[0])
      id_end = int(ids[1])
    else:
      raise ValueError(f"Invalid id format {ids}")

    yield from range(id_begin, id_end + 1)