from io import StringIO
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Generator
from PIL.Image import Image
from pdfplumber import open
from doc_page_extractor import DocExtractor
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

def stream_pdf(doc_extractor: DocExtractor, pdf_file: str) -> Generator[PDFItem, None, None]:
  generator = _extract_page_result(doc_extractor, pdf_file)
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

def _extract_page_result(doc_extractor: DocExtractor, pdf_file: str):
  with open(pdf_file) as pdf:
    for page in pdf.pages:
      image = page.to_image().annotated
      yield doc_extractor.extract(image, "ch")

def _text(texts: Iterable[str]) -> str:
  buffer = StringIO()
  for text in texts:
    text = text.strip()
    buffer.write(text)
  return buffer.getvalue()