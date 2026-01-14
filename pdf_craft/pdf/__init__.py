from .handler import DefaultPDFDocument, DefaultPDFHandler, PDFDocument, PDFHandler
from .ocr import OCR, OCREvent, OCREventKind
from .page_ref import pdf_pages_count
from .ref import *
from .types import (
    DeepSeekOCRSize,
    Page,
    PageLayout,
    PDFDocumentMetadata,
    decode,
    encode,
)
