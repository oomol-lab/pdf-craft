import os
import io

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import Image
from pdfplumber import open
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
  for items in extract(generator):
    for item in items:
      if isinstance(item, ExtractedTitle):
        yield Text(
          text=_text(item.texts),
          kind=TextKind.TITLE,
        )
      elif isinstance(item, ExtractedPlainText):
        yield Text(
          text=_text(item.texts),
          kind=TextKind.PLAIN_TEXT,
        )
      elif isinstance(item, ExtractedFigure):
        yield item.image
      elif isinstance(item, ExtractedTable):
        yield item.image
      elif isinstance(item, ExtractedFormula):
        yield item.image

def _extract_page_result(doc_extractor: DocExtractor, pdf_file: str, debug_output: str | None = None):
  if debug_output is not None:
    os.makedirs(debug_output, exist_ok=True)

  with open(pdf_file) as pdf:
    for i, page in enumerate(pdf.pages):
      image = page.to_image().annotated
      result = doc_extractor.extract(image, "ch")
      if debug_output is not None:
        _generate_plot(image, i, result, debug_output)
      yield result

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