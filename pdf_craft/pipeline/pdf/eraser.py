"""Visual source-text erasure for PDF replacement.

This deliberately remains a simple rectangular layer.  Image segmentation and
content-aware inpainting belong to a future eraser implementation, not the
paragraph text filler.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from .geometry import PageRectangle, region_in_page_points
from .models import PDFReplacementRegion


@dataclass(frozen=True)
class EraseRectangle:
    """One visual mask in PDF-point coordinates with a top-left origin."""

    page_index: int
    rectangle: PageRectangle


class RectangularEraser:
    """Plan and paint the legacy white rectangle masks independently of text."""

    def plan(
        self,
        regions: Iterable[PDFReplacementRegion],
        page_sizes: dict[int, tuple[float, float]],
    ) -> tuple[EraseRectangle, ...]:
        rectangles: list[EraseRectangle] = []
        for region in regions:
            page_width, page_height = page_sizes[region.page_index]
            rectangles.append(EraseRectangle(
                region.page_index,
                region_in_page_points(region, page_width, page_height),
            ))
        return tuple(rectangles)

    @staticmethod
    def draw(overlay, rectangles: Iterable[EraseRectangle], page_height: float) -> None:
        """Draw the masks on a ReportLab overlay (whose origin is bottom-left)."""
        overlay.setFillColorRGB(1, 1, 1)
        for erase in rectangles:
            rectangle = erase.rectangle
            overlay.rect(
                rectangle.x,
                page_height - rectangle.bottom,
                rectangle.width,
                rectangle.height,
                stroke=0,
                fill=1,
            )
