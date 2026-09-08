"""Coordinate conversion shared by PDF patch layers."""

from dataclasses import dataclass

from .models import PDFReplacementRegion


@dataclass(frozen=True)
class PageRectangle:
    """A rectangle in PDF points using a top-left origin for Qt drawing."""

    x: float
    top: float
    width: float
    height: float

    @property
    def bottom(self) -> float:
        return self.top + self.height


def region_in_page_points(
    region: PDFReplacementRegion, page_width: float, page_height: float,
) -> PageRectangle:
    """Map OCR pixels to PDF points, retaining the OCR top-left convention."""
    pixel_width, pixel_height = region.page_pixel_size
    left, top, right, bottom = region.bbox
    scale_x = page_width / pixel_width
    scale_y = page_height / pixel_height
    return PageRectangle(
        x=left * scale_x,
        top=top * scale_y,
        width=(right - left) * scale_x,
        height=(bottom - top) * scale_y,
    )
