import os
import io
import fitz

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL import ImageDraw
from PIL.Image import frombytes, Image
from pdfplumber import open
from pdfplumber.page import Page
from doc_page_extractor import plot, Rectangle, DocExtractor, PreOCRFragment, ExtractedResult
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

  with open(pdf_file) as pdf:
    for i, page in enumerate(pdf.pages):
      dpi = 300 # for scanned book pages
      image = page.to_image(resolution=dpi).annotated
      result = doc_extractor.extract(
        image=image,
        lang="ch",
        adjust_rotation=False,
        adjust_points=False,
        pre_fragments=list(_extract_pre_ocr_fragments(i, image, page)),
      )
      if debug_output is not None:
        _generate_plot(image, i, result, debug_output)
      yield result

def _extract_pre_ocr_fragments(i: int, image: Image, page: Page) -> Generator[PreOCRFragment, None, None]:
  image_width = float(image.size[0])
  image_height = float(image.size[1])

  with fitz.open("/Users/taozeyu/Downloads/中国古代练丹家的目的.pdf") as doc:
    p = doc.load_page(i)
    mat = fitz.Matrix(300 / 72, 300 / 72)
    pixmap = p.get_pixmap(matrix=mat)
    image_p = frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
    draw = ImageDraw.Draw(image_p, mode="RGBA")
    page_width = p.rect.width
    page_height = p.rect.height

    def handle_x(x: float) -> float:
      return (x / page_width) * image_width

    def handle_y(y: float) -> float:
      return (y / page_height) * image_height

    for e in p.get_text("words"):
      x0, y0, x1, y1, text, _1, _2, _3 = e
      x0: float = handle_x(e[0])
      y0: float = handle_y(e[1])
      x1: float = handle_x(e[2])
      y1: float = handle_y(e[3])

  # for word in page.extract_words():
    # x0: float = handle_x(word["x0"])
    # y0: float = handle_y(word["top"])
    # x1: float = handle_x(word["x1"])
    # y1: float = handle_y(word["bottom"])
      react = Rectangle(
        lt=(x0, y0),
        rt=(x1, y0),
        lb=(x0, y1),
        rb=(x1, y1),
      )
      draw.polygon([p for p in react], outline=(255,0,0), width=3)
      yield PreOCRFragment(
        text=text,
        rect=react,
      )
      image_p.save(f"/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/output/test_{i}.png")

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
  image_path = os.path.join(debug_output, f"plot_{index + 1}.png")
  plot_image.save(image_path)