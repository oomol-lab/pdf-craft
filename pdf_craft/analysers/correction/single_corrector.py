import shutil

from pathlib import Path
from xml.etree.ElementTree import Element

from ..reference import samples, NumberStyle
from ..data import Paragraph, Layout, Line
from ..utils import Partition, PartitionTask
from .common import State, Corrector
from .paragraphs_reader import ParagraphsReader


class SingleCorrector(Corrector):
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
        request_path / _chunk_name(begin, end),
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
      chunk_element = self._correct_request(
        reader=reader,
        request_element=request_element,
        is_footnote=is_footnote,
      )
      self.ctx.write_xml_file(
        file_path=request_path / _chunk_name(begin, end),
        xml=chunk_element,
      )

  def _correct_request(self, reader: ParagraphsReader, request_element: Element, is_footnote: bool) -> Element:
    resp_element = self.llm.request_xml(
      template_name="correction/single",
      user_data=request_element,
      params={
        "layouts_count": 4,
        "is_footnote": is_footnote,
        "marks": samples(NumberStyle.CIRCLED_NUMBER, 6),
      },
    )
    raw_lines_list = list(self.extract_lines(request_element))
    corrected_lines_dict = dict(self.extract_lines(resp_element))
    paragraphs: dict[tuple[int, int], Paragraph] = {}

    for index, _ in sorted(raw_lines_list, key=lambda x: x[0]):
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
      if corrected_lines is not None:
        self._apply_corrected_lines(
          paragraph=paragraph,
          index=index,
          corrected_lines=corrected_lines,
        )

    chunk_element = Element("chunk")

    for para_index in sorted(list(paragraphs.keys())):
      page_index, order_index = para_index
      paragraph = paragraphs[para_index]
      paragraph_element = paragraph.to_xml()
      paragraph_element.set("page-index", str(page_index))
      paragraph_element.set("order-index", str(order_index))
      chunk_element.append(paragraph_element)

    return chunk_element

  def _apply_corrected_lines(
        self,
        paragraph: Paragraph,
        index: tuple[int, int],
        corrected_lines: list[str],
      ) -> None:

    layout: Layout | None = None
    for got_layout in paragraph.layouts:
      if index == (got_layout.page_index, got_layout.order_index):
        layout = got_layout
    if layout is None:
      return None
    layout.lines = [
      Line(
        text=line_text,
        confidence="1.0",
      )
      for line_text in corrected_lines
    ]

def _chunk_name(begin: tuple[int, int], end: tuple[int, int]) -> str:
  return f"chunk_{begin[0]}_{begin[1]}_{end[0]}_{end[1]}.xml"