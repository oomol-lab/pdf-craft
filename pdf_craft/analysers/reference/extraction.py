from pathlib import Path
from typing import Generator

from ..sequence import read_paragraphs, Paragraph, Layout, Line
from .mark import transform2mark, Mark

ExtractedFootnote = tuple[Mark | None, list[Layout]]

def extract_footnote_references(footnote_path: Path) -> Generator[ExtractedFootnote, None, None]:
  for paragraph in read_paragraphs(footnote_path):
    for mark, lines in _extract_footnote_from_paragraph(paragraph):
      yield mark, _transform2layouts(lines)

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