from pathlib import Path
from shutil import rmtree
from typing import Generator
from xml.etree.ElementTree import Element

from ...xml import encode
from .extraction import extract_footnote_references, ExtractedFootnote


def generate_footnote_references(sequence_path: Path, output_path: Path):
  if output_path.exists():
    rmtree(output_path)
  output_path.mkdir(parents=True, exist_ok=True)

  # TODO: 检查每页的内容形式上是否完整，若有问题，交给 LLM 解决
  for page_index, buffer in _extract_and_split_by_pages(sequence_path):
    page_element = Element("page")
    page_element.set("page-index", str(page_index))

    for mark, layouts in buffer:
      if mark is None:
        continue # TODO: 这种应该由 LLM 来处理，没实现之前只能跳过
      footnote_element = Element("footnote")
      mark_element = Element("mark")
      mark_element.text = mark.char
      footnote_element.append(mark_element)
      page_element.append(footnote_element)
      for layout in layouts:
        footnote_element.append(layout.to_xml())

    if len(page_element) > 0:
      file_path = output_path / f"page_{page_index}.xml"
      with open(file_path, "w", encoding="utf-8") as file:
        file.write(encode(page_element))

def _extract_and_split_by_pages(sequence_path: Path) -> Generator[tuple[int, list[ExtractedFootnote]], None, None]:
  page_index = -1
  buffer: list[ExtractedFootnote] = []
  for mark, layouts in extract_footnote_references(sequence_path):
    current_page_index = layouts[0].page_index
    if buffer and page_index != current_page_index:
      yield page_index, buffer
      page_index = current_page_index
      buffer = []
    buffer.append((mark, layouts))
  if buffer:
    yield page_index, buffer