import os
import io
import fitz

from typing import Literal
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import frombytes, Image
from doc_page_extractor import plot, DocExtractor, ExtractedResult
from .rough_extractor import (
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

class PDFPageExtractor:
  def __init__(
      self,
      device: Literal["cpu", "cuda"],
      model_dir_path: str,
      debug_dir_path: str | None = None,
    ):
    self._debug_dir_path: str | None = debug_dir_path
    self._doc_extractor = DocExtractor(
      device=device,
      model_dir_path=model_dir_path,
      order_by_layoutreader=False,
    )

  def extract(self, pdf_path: str) -> Generator[PDFItem, None, None]:
    current_plain_text: ExtractedPlainText | None = None

    for items in extract(self._extract_page_result(pdf_path)):
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
              text=self._text(current_plain_text.texts),
              kind=TextKind.PLAIN_TEXT,
            )
            current_plain_text = item

        else:
          if current_plain_text is not None:
            yield Text(
              text=self._text(current_plain_text.texts),
              kind=TextKind.PLAIN_TEXT,
            )
            current_plain_text = None

          if isinstance(item, ExtractedTitle):
            yield Text(
              text=self._text(item.texts),
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
        text=self._text(current_plain_text.texts),
        kind=TextKind.PLAIN_TEXT,
      )
      current_plain_text = None

  def _extract_page_result(self, pdf_file: str):
    if self._debug_dir_path is not None:
      os.makedirs(self._debug_dir_path, exist_ok=True)

    with fitz.open(pdf_file) as pdf:
      for i, page in enumerate(pdf.pages()):
        dpi = 300 # for scanned book pages
        image = self._page_screenshot_image(page, dpi)
        result = self._doc_extractor.extract(
          image=image,
          lang="ch",
          adjust_points=False,
        )
        if self._debug_dir_path is not None:
          self._generate_plot(image, i, result, self._debug_dir_path)
        yield result

  def _text(self, texts: Iterable[str]) -> str:
    buffer = io.StringIO()
    for text in texts:
      text = text.strip()
      buffer.write(text)
    return buffer.getvalue()

  def _page_screenshot_image(self, page: fitz.Page, dpi: int):
    default_dpi = 72
    matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
    pixmap = page.get_pixmap(matrix=matrix)
    return frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

  def _generate_plot(self, image: Image, index: int, result: ExtractedResult, plot_path: str):
    plot_image: Image
    if result.adjusted_image is None:
      plot_image = image.copy()
    else:
      plot_image = result.adjusted_image

    plot(plot_image, result.layouts)
    os.makedirs(plot_path, exist_ok=True)
    image_path = os.path.join(plot_path, f"plot_{index + 1}.png")
    plot_image.save(image_path)