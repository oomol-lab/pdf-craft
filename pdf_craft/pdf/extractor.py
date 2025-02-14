from typing import Generator
from dataclasses import dataclass
from PIL.Image import Image
from doc_page_extractor import clip, Layout, LayoutClass, Rectangle, ExtractedResult
from .section import Section


@dataclass
class ExtractedTitle:
  texts: list[str]
  rects: list[Rectangle]

@dataclass
class ExtractedPlainText:
  texts: list[str]
  rects: list[Rectangle]
  has_paragraph_indentation: bool
  last_line_touch_end: bool

@dataclass
class ExtractedFigure:
  image: Image

@dataclass
class ExtractedTable:
  image: Image

@dataclass
class ExtractedFormula:
  image: Image

ExtractedItem = ExtractedTitle | ExtractedPlainText | ExtractedFigure | ExtractedTable | ExtractedFormula

def extract(generator: Generator[ExtractedResult, None, None]) -> Generator[list[ExtractedItem], None, None]:
  for result, section in _extract_results_and_sections(generator):
    framework_layouts = section.framework()
    extracted_items: list[ExtractedItem] = []
    for layout in result.layouts:
      if layout in framework_layouts:
        continue
      extracted_item = _map_to_extracted_item(result, layout)
      if extracted_item is None:
        continue
      extracted_items.append(extracted_item)
    yield extracted_items

def _extract_results_and_sections(generator: Generator[ExtractedResult, None, None]):
  max_len = 2 # section can be viewed up to 2 pages back
  queue: list[tuple[ExtractedResult, Section]] = []

  for result in generator:
    section = Section(result.layouts)
    for i, (_, pre_section) in enumerate(queue):
      offset = len(queue) - i
      pre_section.link_next(section, offset)

    queue.append((result, section))
    if len(queue) > max_len:
      yield queue.pop(0)

  for result, section in queue:
    yield result, section

def _map_to_extracted_item(result: ExtractedResult, layout: Layout) -> ExtractedItem | None:
  if layout.cls == LayoutClass.TITLE:
    return ExtractedTitle(
      texts=[f.text for f in layout.fragments],
      rects=[f.rect for f in layout.fragments],
    )
  elif layout.cls == LayoutClass.PLAIN_TEXT:
    return _to_extracted_plan_text(layout)

  elif layout.cls == LayoutClass.FIGURE:
    return ExtractedFigure(
      image=clip(result, layout),
    )
  elif layout.cls == LayoutClass.TABLE:
    return ExtractedTable(
      image=clip(result, layout),
    )
  elif layout.cls == LayoutClass.ISOLATE_FORMULA:
    return ExtractedFormula(
      image=clip(result, layout),
    )
  else:
    return None

def _to_extracted_plan_text(layout: Layout) -> ExtractedPlainText:
  mean_line_height: float = 0.0
  x1: float = float("inf")
  y1: float = float("inf")
  x2: float = float("-inf")
  y2: float = float("-inf")

  for fragment in layout.fragments:
    mean_line_height += fragment.rect.size[1]
    for x, y in fragment.rect:
      x1 = min(x1, x)
      y1 = min(y1, y)
      x2 = max(x2, x)
      y2 = max(y2, y)

  has_paragraph_indentation: bool = False
  last_line_touch_end: bool = False

  if len(layout.fragments) > 0:
    mean_line_height /= len(layout.fragments)
    first_fragment = layout.fragments[0]
    first_fragment_delta_x = (first_fragment.rect.lt[0] + first_fragment.rect.lb[0]) / 2 - x1
    has_paragraph_indentation = first_fragment_delta_x > mean_line_height
    last_fragment = layout.fragments[-1]
    last_fragment_delta_x = x2 - (last_fragment.rect.rt[0] + last_fragment.rect.rb[0]) / 2
    last_line_touch_end = last_fragment_delta_x < mean_line_height

  return ExtractedPlainText(
    texts=[f.text for f in layout.fragments],
    rects=[f.rect for f in layout.fragments],
    has_paragraph_indentation=has_paragraph_indentation,
    last_line_touch_end=last_line_touch_end,
  )
