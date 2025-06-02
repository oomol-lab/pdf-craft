import shutil

from pathlib import Path
from xml.etree.ElementTree import Element

from ..reference import samples, NumberStyle
from ..utils import Partition
from .common import State, Corrector


class SingleCorrector(Corrector):
  def do(self, from_path: Path, request_path: Path, is_footnote: bool) -> None:
    request_path.mkdir(parents=True, exist_ok=True)
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
      for task in partition.pop_tasks():
        with task:
          begin = task.begin
          end = task.end
          request_element = task.payload
          chunk_element = self._correct_request(
            request_element=request_element,
            is_footnote=is_footnote,
          )
          self.ctx.write_xml_file(
            file_path=request_path / _chunk_name(begin, end),
            xml=chunk_element,
          )

  def _correct_request(self, request_element: Element, is_footnote: bool) -> Element:
    resp_element = self.llm.request_xml(
      template_name="correction/single",
      user_data=request_element,
      params={
        "layouts_count": 4,
        "is_footnote": is_footnote,
        "marks": samples(NumberStyle.CIRCLED_NUMBER, 6),
      },
    )
    return resp_element

def _chunk_name(begin: tuple[int, int], end: tuple[int, int]) -> str:
  return f"chunk_{begin[0]}_{begin[1]}_{end[0]}_{end[1]}.xml"