"""PDF replacement data shared by erasing, filling and composition."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PDFInlineFormula:
    """One inline LaTeX atom embedded in a replacement paragraph.

    ``PDFReplacement.text`` stores one object-replacement marker for every
    instance, in the same order as this tuple.  Keeping the source separate
    from ordinary text lets the PDF filler choose a real vector fragment or a
    readable text fallback without leaking LaTeX delimiters into the output.
    """

    latex: str


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
    inline_formulas: tuple[PDFInlineFormula, ...] = ()
    # Non-text source geometry (figures, tables, display formulas, …) which
    # must remain clear when the text fitter uses the gap below a text bbox.
    # Text regions are collected globally by the window planner, so callers
    # only need to attach obstacles that are not themselves replacements.
    obstacle_regions: tuple[PDFReplacementRegion, ...] = ()

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
