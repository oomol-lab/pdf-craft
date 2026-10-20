"""Public paragraph content and source coordinates, independent of renderers."""
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .access import PageGeometry


@dataclass(frozen=True)
class SourceRegion:
    """One original PCEX fragment, in OCR pixels, in paragraph reading order."""
    page: PageGeometry
    source_order_in_page: int
    bbox: tuple[int, int, int, int]

    def map_bbox(self, target_size: tuple[int, int]) -> tuple[float, float, float, float]:
        """Scale into an equally cropped/oriented image; never infer scale from DPI."""
        width, height = target_size
        if width <= 0 or height <= 0 or self.page.width <= 0 or self.page.height <= 0:
            raise ValueError("source and target page dimensions must be positive")
        left, top, right, bottom = self.bbox
        sx, sy = width / self.page.width, height / self.page.height
        return left * sx, top * sy, right * sx, bottom * sy


RegionT = TypeVar("RegionT", bound=SourceRegion)


@dataclass(frozen=True)
class ParagraphContent(Generic[RegionT]):
    """One whole paragraph. text/original are Markdown, never a bilingual merge."""
    text: str
    original: str | None
    regions: list[RegionT]


@dataclass(frozen=True)
class TextLine:
    """A frozen Qt line/run, split at formulas; absolute source-page pixels."""
    text: str
    x: float
    baseline: float


@dataclass(frozen=True)
class FormulaPlacement:
    """An inline formula, in source pixels; PDF bytes are the actual vector atom."""
    latex: str
    x: float
    baseline: float
    descent: float
    pdf: bytes
    scale_x: float
    scale_y: float


@dataclass(frozen=True)
class EraseArea:
    """Actual source-text mask, in source pixels, with its sampled RGB color."""
    bbox: tuple[float, float, float, float]
    color: tuple[int, int, int]


@dataclass(frozen=True)
class PDFSourceRegion(SourceRegion):
    """Final layout for this source; an unused region has no size and no lines."""
    font_size: float | None
    lines: list[TextLine]
    formulas: list[FormulaPlacement] = field(default_factory=list)
    erasures: list[EraseArea] = field(default_factory=list)


@dataclass(frozen=True)
class PDFParagraphContent(ParagraphContent[PDFSourceRegion]):
    """One complete paragraph after all its regions have final page typography."""
