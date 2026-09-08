"""PDF replacement data shared by erasing, filling and composition."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PDFReplacementRegion:
    """One ordered OCR rectangle belonging to a logical paragraph."""

    page_index: int
    bbox: tuple[int, int, int, int]
    page_pixel_size: tuple[int, int]
    dpi: int = 300
    reading_order: int = 0


@dataclass(frozen=True)
class PDFReplacement:
    """Translated paragraph and the ordered source rectangles it replaces.

    The top-level geometry remains the first region for compatibility with
    callers that construct a one-rectangle replacement directly.  New callers
    should populate ``regions`` for every source rectangle in the paragraph.
    """

    page_index: int
    bbox: tuple[int, int, int, int]
    text: str
    page_pixel_size: tuple[int, int]
    dpi: int = 300
    reading_order: int = 0
    regions: tuple[PDFReplacementRegion, ...] = ()
    layout_ref: str = "text"
    layout_level: int = 0

    def source_regions(self) -> tuple[PDFReplacementRegion, ...]:
        """Return explicit paragraph regions or the legacy single region."""
        if self.regions:
            return self.regions
        return (PDFReplacementRegion(
            self.page_index,
            self.bbox,
            self.page_pixel_size,
            self.dpi,
            self.reading_order,
        ),)


@dataclass(frozen=True)
class PDFSkippedReplacement:
    """An explicitly skipped overflow, retained for callers to inspect."""

    page_index: int
    bbox: tuple[int, int, int, int]
    reason: str
