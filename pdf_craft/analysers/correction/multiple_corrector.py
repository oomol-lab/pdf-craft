import shutil

from pathlib import Path
from xml.etree.ElementTree import Element

from ..data import Paragraph, AssetLayout
from ..utils import Partition, PartitionTask
from .common import State, Corrector
from .repeater import repeat_multiple_correct
from .paragraphs_reader import ParagraphsReader


class MultipleCorrector(Corrector):

  def do(self, from_path: Path, request_path: Path, is_footnote: bool) -> None:
    request_path.mkdir(parents=True, exist_ok=True)
    self.ctx.reporter.set(
      max_count=sum(1 for _ in self.generate_request_xml(from_path)),
    )
    partition: Partition[tuple[int, int], State, Element] = Partition(
      dimension=2,
      context=self.ctx,
      sequence=self.generate_request_xml(from_path),
      done=lambda _, __: self.ctx.reporter.increment(),
      remove=lambda begin, end: shutil.rmtree(
        request_path / _file_name("steps", begin, end),
      ),
    )
    with partition:
      self.threads.run(
        next_task=partition.pop_task,
        thread_payload=lambda: ParagraphsReader(from_path),
        invoke=lambda reader, task: self._emit_request(
          task=task,
          reader=reader,
          request_path=request_path,
          is_footnote=is_footnote,
        ),
      )

  def _emit_request(
        self,
        task: PartitionTask[tuple[int, int], State, Element],
        reader: ParagraphsReader,
        request_path: Path,
        is_footnote: bool,
      ) -> Element:

    with task:
      begin = task.begin
      end = task.end
      request_element = task.payload
      resp_element = repeat_multiple_correct(
        llm=self.llm,
        context=self.ctx,
        save_path=request_path / _file_name("steps",begin, end),
        raw_request=request_element,
        is_footnote=is_footnote,
      )
      self._apply_updation(
        reader=reader,
        request_path=request_path,
        request_element=request_element,
        resp_element=resp_element,
      )

  def _apply_updation(
        self,
        request_path: Path,
        reader: ParagraphsReader,
        request_element: Element,
        resp_element: Element,
      ) -> None:

    raw_lines_list = list(self.extract_lines(request_element))
    if not raw_lines_list:
      return

    corrected_lines_dict = dict(self.extract_lines(resp_element))
    paragraphs: dict[tuple[int, int], Paragraph] = {}

    for index, raw_lines in sorted(raw_lines_list, key=lambda x: x[0]):
      page_index, order_index = index
      paragraph = reader.read(
        layout_index=(page_index, order_index),
      )
      if paragraph is None:
        continue

      para_index = (paragraph.page_index, paragraph.order_index)
      if para_index not in paragraphs:
        paragraphs[para_index] = paragraph

      corrected_lines = corrected_lines_dict.get(index, None)
      if corrected_lines is None:
        corrected_lines = raw_lines

      self._apply_paragraph_lines(
        paragraph=paragraph,
        lines=corrected_lines,
      )

    chunk_element = Element("chunk")

    for para_index in sorted(list(paragraphs.keys())):
      page_index, order_index = para_index
      paragraph = paragraphs[para_index]
      if not paragraph.layouts:
        continue
      paragraph_element = paragraph.to_xml()
      paragraph_element.set("page-index", str(page_index))
      paragraph_element.set("order-index", str(order_index))
      chunk_element.append(paragraph_element)

    begin, _ = raw_lines_list[0]
    end, _ = raw_lines_list[-1]
    file_name = _file_name("chunk", begin, end) + ".xml"
    self.ctx.write_xml_file(
      file_path=request_path / file_name,
      xml=chunk_element,
    )

  def _apply_paragraph_lines(self, paragraph: Paragraph, lines: list[str]):
    next_line_index: int = 0
    limit_length: int = -1

    for i, layout in enumerate(paragraph.layouts):
      for j, line in enumerate(layout.lines):
        if next_line_index >= len(lines):
          limit_length = j
          break
        line.text = lines[next_line_index]
        next_line_index += 1

      if limit_length < 0:
        continue
      if isinstance(layout, AssetLayout):
        continue

      layout.lines = layout.lines[:limit_length]
      if layout.lines:
        limit_length = i + 1
      else:
        limit_length = i

    if limit_length != -1:
      paragraph.layouts = paragraph.layouts[:limit_length]

def _file_name(name: str, begin: tuple[int, int], end: tuple[int, int]) -> str:
  return f"{name}_{begin[0]}_{begin[1]}_{end[0]}_{end[1]}"