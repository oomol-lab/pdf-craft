import fitz

from typing import Generator, Iterable, Sequence
from pathlib import Path
from dataclasses import dataclass
from PIL.Image import frombytes, Image
from doc_page_extractor import plot, Layout, ExtractedResult, TableLayoutParsedFormat
from .section import Section
from .protocol import DocExtractorProtocol
from .types import OCRLevel, PDFPageExtractorProgressReport


# section can be viewed up to 2 pages back
_MAX_VIEWED_PAGES: int = 2

@dataclass
class DocumentParams:
  pdf: str | fitz.Document
  page_indexes: Iterable[int] | None
  report_progress: PDFPageExtractorProgressReport | None

class DocumentExtractor:
  def __init__(
        self,
        doc_extractor: DocExtractorProtocol,
        ocr_level: OCRLevel,
        extract_formula: bool,
        extract_table_format: TableLayoutParsedFormat | None,
        debug_dir_path: Path | None,
      ) -> None:

    self._debug_dir_path: Path | None = debug_dir_path
    self._doc_extractor: DocExtractorProtocol = doc_extractor
    self._extract_formula: bool = extract_formula
    self._extract_table_format: TableLayoutParsedFormat | None = extract_table_format
    self._ocr_for_each_layouts: bool = (ocr_level == OCRLevel.OncePerLayout)

  def extract(self, params: DocumentParams) -> Generator[tuple[int, ExtractedResult, list[Layout]], None, None]:
    for result, section in self._extract_results_and_sections(params):
      framework_layouts = section.framework()
      yield section.page_index, result, [
        layout for layout in result.layouts
        if layout not in framework_layouts
      ]

  def prepare_models(self) -> None:
    self._doc_extractor.prepare_models()

  def _extract_results_and_sections(self, params: DocumentParams):
    queue: list[tuple[ExtractedResult, Section]] = []

    for page_index, result in self._extract_page_result(params):
      section = Section(page_index, result.layouts)
      for i, (_, pre_section) in enumerate(queue):
        offset = len(queue) - i
        pre_section.link_next(section, offset)

      queue.append((result, section))
      if len(queue) > _MAX_VIEWED_PAGES:
        yield queue.pop(0)

    for result, section in queue:
      yield result, section

  def _extract_page_result(self, params: DocumentParams):
    if self._debug_dir_path is not None:
      self._debug_dir_path.mkdir(parents=True, exist_ok=True)

    document: fitz.Document
    should_close = False
    report_progress = params.report_progress

    if isinstance(params.pdf, str):
      document = fitz.open(params.pdf)
      should_close = True
    else:
      document = params.pdf

    scan_indexes, enable_indexes = self._page_indexes_range(
      document=document,
      page_indexes=params.page_indexes,
    )
    try:
      for i, page_index in enumerate(scan_indexes):
        dpi = 300 # for scanned book pages
        page = document.load_page(page_index)
        image = self._page_screenshot_image(page, dpi)
        result = self._doc_extractor.extract(
          image=image,
          extract_formula=self._extract_formula,
          extract_table_format=self._extract_table_format,
          ocr_for_each_layouts=self._ocr_for_each_layouts,
          adjust_points=False,
        )
        if self._debug_dir_path is not None:
          self._generate_plot(
            image=image,
            index=page_index,
            result=result,
            plot_path=self._debug_dir_path,
          )
        if page_index in enable_indexes:
          yield page_index, result

        if report_progress is not None:
          report_progress(i + 1, len(scan_indexes))

    finally:
      if should_close:
        document.close()

  def _page_indexes_range(self, document: fitz.Document, page_indexes: Iterable[int] | None) -> tuple[Sequence[int], Sequence[int]]:
    pages_count = document.page_count
    if page_indexes is None:
      return range(pages_count), range(pages_count)

    enable_set: set[int] = set()
    scan_set: set[int] = set()

    for i in page_indexes:
      if 0 <= i < document.page_count:
        enable_set.add(i)
        for j in range(i - _MAX_VIEWED_PAGES, i + _MAX_VIEWED_PAGES + 1):
          if 0 <= j < document.page_count:
            scan_set.add(j)

    enable_list: list[int] = list(enable_set)
    scan_list: list[int] = list(scan_set)
    enable_list.sort()
    scan_list.sort()

    return enable_list, scan_list

  def _page_screenshot_image(self, page: fitz.Page, dpi: int):
    default_dpi = 72
    matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
    pixmap = page.get_pixmap(matrix=matrix)
    return frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

  def _generate_plot(self, image: Image, index: int, result: ExtractedResult, plot_path: Path):
    plot_image: Image
    if result.adjusted_image is None:
      plot_image = image.copy()
    else:
      plot_image = result.adjusted_image

    plot(plot_image, result.layouts)
    plot_path.mkdir(parents=True, exist_ok=True)
    image_path = plot_path / f"plot_{index + 1}.png"
    plot_image.save(image_path)