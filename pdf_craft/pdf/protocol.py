from typing import Protocol
from PIL.Image import Image
from doc_page_extractor import TableLayoutParsedFormat, ExtractedResult


class DocExtractorProtocol(Protocol):
  def prepare_models(self) -> None:
    raise NotImplementedError("must implement prepare_models method")

  def extract(
    self,
    image: Image,
    extract_formula: bool,
    extract_table_format: TableLayoutParsedFormat | None = None,
    ocr_for_each_layouts: bool = False,
    adjust_points: bool = False
  ) -> ExtractedResult:
    raise NotImplementedError("must implement extract method")