from os import PathLike
from pathlib import Path
from typing import Generator

from PIL.Image import Image

from ..error import PDFError
from .handler import DefaultPDFHandler, PDFDocument, PDFHandler


def pdf_pages_count(
    pdf_path: PathLike | str,
    pdf_handler: PDFHandler | None = None,
) -> int:
    if pdf_handler is None:
        pdf_handler = DefaultPDFHandler()
    document: PDFDocument | None = None
    try:
        document = pdf_handler.open(pdf_path=Path(pdf_path))
        return document.pages_count
    except PDFError:
        raise
    except Exception as error:
        raise PDFError("Failed to parse PDF document.", page_index=None) from error
    finally:
        if document is not None:
            document.close()


class PageRefContext:
    def __init__(
        self,
        pdf_path: Path,
        pdf_handler: PDFHandler,
    ) -> None:
        self._pdf_path = pdf_path
        self._pdf_handler: PDFHandler = pdf_handler
        self._document: PDFDocument | None = None

    @property
    def pages_count(self) -> int:
        assert self._document is not None
        return self._document.pages_count

    def __enter__(self) -> "PageRefContext":
        assert self._document is None
        try:
            self._document = self._pdf_handler.open(self._pdf_path)
        except PDFError:
            raise
        except Exception as error:
            raise PDFError("Failed to open PDF document.", page_index=None) from error
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None

    def __iter__(self) -> Generator["PageRef", None, None]:
        assert self._document is not None
        for i in range(self._document.pages_count):
            yield PageRef(
                document=self._document,
                page_index=i + 1,
            )


_PNG_COMPRESSION_RATIO = 0.5  # Conservative estimate for document images
_BYTES_PER_PIXEL = 3  # RGB


class PageRef:
    def __init__(
        self,
        document: PDFDocument,
        page_index: int,
    ) -> None:
        self._document = document
        self._page_index = page_index

    @property
    def page_index(self) -> int:
        return self._page_index

    def render(self, dpi: int, max_image_file_size: int | None = None) -> Image:
        try:
            if max_image_file_size is not None:
                width_inch, height_inch = self._document.page_size(self._page_index)
                max_dpi = round(
                    self._dpi_with_size(
                        file_size=max_image_file_size,
                        width_inch=width_inch,
                        height_inch=height_inch,
                    )
                )
                dpi = min(dpi, max_dpi)

            return self._document.render_page(
                page_index=self._page_index,
                dpi=dpi,
            )
        except PDFError as error:
            error.page_index = self._page_index
            raise error

        except Exception as error:
            raise PDFError(
                f"Failed to render page {self._page_index}.",
                page_index=self._page_index,
            ) from error

    def _dpi_with_size(
        self, file_size: int, width_inch: float, height_inch: float
    ) -> float:
        # Formula: file_size = width_px * height_px * bytes_per_pixel * compression_ratio
        # where width_px = width_inch * dpi, height_px = height_inch * dpi
        return (
            file_size
            / (width_inch * height_inch * _BYTES_PER_PIXEL * _PNG_COMPRESSION_RATIO)
        ) ** 0.5
