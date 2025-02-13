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
    return ExtractedPlainText(
      texts=[f.text for f in layout.fragments],
      rects=[f.rect for f in layout.fragments],
    )
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