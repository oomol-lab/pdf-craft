import os
import io
import fitz

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import frombytes, Image
from doc_page_extractor import plot, DocExtractor, ExtractedResult
from .extractor import (
  extract,
  ExtractedTitle,
  ExtractedPlainText,
  ExtractedFigure,
  ExtractedTable,
  ExtractedFormula,
)


class TextKind(Enum):
  TITLE = 0
  PLAIN_TEXT = 1

@dataclass
class Text:
  text: str
  kind: TextKind

PDFItem = Text | Image

def stream_pdf(doc_extractor: DocExtractor, pdf_file: str, debug_output: str | None = None) -> Generator[PDFItem, None, None]:
  generator = _extract_page_result(doc_extractor, pdf_file, debug_output)
  current_plain_text: ExtractedPlainText | None = None

  for items in extract(generator):
    for item in items:
      if isinstance(item, ExtractedPlainText):
        if current_plain_text is None:
          current_plain_text = item
        elif current_plain_text.last_line_touch_end and not item.has_paragraph_indentation:
          current_plain_text.texts.extend(item.texts)
          current_plain_text.rects.extend(item.rects)
          current_plain_text.last_line_touch_end = item.last_line_touch_end
        else:
          yield Text(
            text=_text(current_plain_text.texts),
            kind=TextKind.PLAIN_TEXT,
          )
          current_plain_text = item

      else:
        if current_plain_text is not None:
          yield Text(
            text=_text(current_plain_text.texts),
            kind=TextKind.PLAIN_TEXT,
          )
          current_plain_text = None

        if isinstance(item, ExtractedTitle):
          yield Text(
            text=_text(item.texts),
            kind=TextKind.TITLE,
          )
        elif isinstance(item, ExtractedFigure):
          yield item.image
        elif isinstance(item, ExtractedTable):
          yield item.image
        elif isinstance(item, ExtractedFormula):
          yield item.image

  if current_plain_text is not None:
    yield Text(
      text=_text(current_plain_text.texts),
      kind=TextKind.PLAIN_TEXT,
    )
    current_plain_text = None

def _extract_page_result(doc_extractor: DocExtractor, pdf_file: str, debug_output: str | None = None):
  if debug_output is not None:
    os.makedirs(debug_output, exist_ok=True)

  with fitz.open(pdf_file) as pdf:
    for i, page in enumerate(pdf.pages()):
      dpi = 300 # for scanned book pages
      image = _page_screenshot_image(page, dpi)
      result = doc_extractor.extract(
        image=image,
        lang="ch",
        adjust_points=False,
      )
      if debug_output is not None:
        _generate_plot(image, i, result, debug_output)
      yield result

def _page_screenshot_image(page: fitz.Page, dpi: int):
  default_dpi = 72
  matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
  pixmap = page.get_pixmap(matrix=matrix)
  return frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

# def _extract_pre_ocr_fragments(image: Image, page: fitz.Page) -> list[PreOCRFragment]:
#   fragments: list[PreOCRFragment] = []
#   page_width = page.rect.width
#   page_height = page.rect.height
#   image_width = float(image.size[0])
#   image_height = float(image.size[1])

#   def handle_x(x: float) -> float:
#     return (x / page_width) * image_width

#   def handle_y(y: float) -> float:
#     return (y / page_height) * image_height

#   for word in page.get_text("words"):
#     x0: float = handle_x(word[0])
#     y0: float = handle_y(word[1])
#     x1: float = handle_x(word[2])
#     y1: float = handle_y(word[3])
#     text: str = word[4]
#     fragments.append(PreOCRFragment(
#       text=text,
#       rect=Rectangle(
#         lt=(x0, y0),
#         rt=(x1, y0),
#         lb=(x0, y1),
#         rb=(x1, y1),
#       ),
#     ))
#   return sorted(fragments, key=lambda fragment: fragment.rect.lt[1])

def _text(texts: Iterable[str]) -> str:
  buffer = io.StringIO()
  for text in texts:
    text = text.strip()
    buffer.write(text)
  return buffer.getvalue()

def _generate_plot(image: Image, index: int, result: ExtractedResult, debug_output: str):
  plot_image: Image
  if result.adjusted_image is None:
    plot_image = image.copy()
  else:
    plot_image = result.adjusted_image

  plot(plot_image, result.layouts)
  os.makedirs(debug_output, exist_ok=True)
  image_path = os.path.join(debug_output, f"plot_{index + 1}.png")
  plot_image.save(image_path)