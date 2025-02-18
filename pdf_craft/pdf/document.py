import os
import fitz

from typing import Generator, Literal
from PIL.Image import frombytes, Image
from doc_page_extractor import plot, Layout, DocExtractor, ExtractedResult
from .section import Section


class DocumentExtractor:
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

  def extract(self, pdf_file_path: str) -> Generator[tuple[ExtractedResult, list[Layout]], None, None]:
    for result, section in self._extract_results_and_sections(pdf_file_path):
      framework_layouts = section.framework()
      yield result, [
        layout for layout in result.layouts
        if layout not in framework_layouts
      ]

  def _extract_results_and_sections(self, pdf_file_path: str):
    max_len = 2 # section can be viewed up to 2 pages back
    queue: list[tuple[ExtractedResult, Section]] = []

    for result in self._extract_page_result(pdf_file_path):
      section = Section(result.layouts)
      for i, (_, pre_section) in enumerate(queue):
        offset = len(queue) - i
        pre_section.link_next(section, offset)

      queue.append((result, section))
      if len(queue) > max_len:
        yield queue.pop(0)

    for result, section in queue:
      yield result, section

  def _extract_page_result(self, pdf_file_path: str):
    if self._debug_dir_path is not None:
      os.makedirs(self._debug_dir_path, exist_ok=True)

    with fitz.open(pdf_file_path) as pdf:
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