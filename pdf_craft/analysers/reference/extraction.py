from pathlib import Path
from typing import Generator

from ..data import Paragraph, Layout, Caption, Line
from ..sequence import read_paragraphs
from .mark import transform2mark, Mark


ExtractedFootnote = tuple[int, Mark, list[Layout]]

def extract_footnote_references(footnote_path: Path) -> Generator[ExtractedFootnote, None, None]:
  pre_page_index: int = -1
  last: ExtractedFootnote | None = None

  for page_index, mark, layouts in _extract_mark_and_layouts(footnote_path):
    if mark is not None:
      if last is not None:
        yield last
      last = (page_index, mark, layouts)
    elif last is not None:
      last_page_index, _, last_layouts = last
      if page_index == last_page_index:
        last_layouts.extend(layouts)
      elif pre_page_index == page_index - 1:
        # this indicates that the footnote of the previous page will continue after turning the page
        last_layouts.extend(layouts)
      else:
        yield last
        last = None
        # TODO: 此时当前 layouts 就会被抛弃。此处代码逻辑上无解，可以考虑交给 LLM 来处理
    pre_page_index = page_index

  if last is not None:
    yield last

def _extract_mark_and_layouts(footnote_path: Path) -> Generator[tuple[int, Mark | None, list[Layout]], None, None]:
  for paragraph in read_paragraphs(footnote_path):
    for mark, lines in _extract_footnote_from_paragraph(paragraph):
      layouts = _transform2layouts(lines)
      page_index = layouts[0].page_index
      yield page_index, mark, layouts

def _extract_footnote_from_paragraph(paragraph: Paragraph) -> Generator[tuple[Mark | None, list[tuple[Layout, Line]]], None, None]:
  current_mark: Mark | None = None
  lines: list[tuple[Layout, Line]] = []

  for layout in paragraph.layouts:
    for line in layout.lines:
      text = line.text.strip()
      mark = text and transform2mark(text[0])
      if isinstance(mark, Mark):
        if lines:
          yield current_mark, lines
          lines = []
        current_mark = mark
        text = text[1:].strip()
      lines.append((layout, Line(
        text=text,
        confidence=line.confidence,
      )))

  if lines:
    yield current_mark, lines

def _transform2layouts(lines: list[tuple[Layout, Line]]) -> list[Layout]:
  layouts: dict[str, tuple[int, Layout]] = {}
  for layout, line in lines:
    lines: list[Line]
    if layout.id not in layouts:
      lines = []
      layouts[layout.id] = (
        len(layouts),
        Layout(
          kind=layout.kind,
          page_index=layout.page_index,
          order_index=layout.order_index,
          caption=Caption(lines=[]),
          lines=lines,
        ),
      )
    else:
      lines = layouts[layout.id][1].lines
    lines.append(line)

  return list(
    line for _, line in sorted(
      list(layouts.values()),
      key=lambda x: x[0],
    )
  )