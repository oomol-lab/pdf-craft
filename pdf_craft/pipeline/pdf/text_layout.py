"""ReportLab paragraph fitting for OCR source rectangles."""

from dataclasses import dataclass
from typing import Any, Literal, cast
from xml.sax.saxutils import escape


Alignment = Literal["left", "center", "right", "justify"]


@dataclass(frozen=True)
class PatchTextOptions:
    """Typography and failure policy for text replacing an OCR bounding box."""

    font_name: str = "STSong-Light"
    max_font_size: float = 12.0
    min_font_size: float = 4.0
    line_height: float = 1.2
    horizontal_padding: float = 1.0
    vertical_padding: float = 1.0
    alignment: Alignment = "left"
    overflow: Literal["error", "skip"] = "error"


@dataclass(frozen=True)
class FittedParagraph:
    """A paragraph proven to fit within a particular rectangle."""

    paragraph: Any
    font_size: float
    width: float
    height: float


class BoxTextLayout:
    """Fit continuous text into a fixed ReportLab rectangle without truncation."""

    def __init__(self, options: PatchTextOptions | None = None) -> None:
        self.options = options or PatchTextOptions()
        self._validate_options()

    def fit(self, text: str, width: float, height: float) -> FittedParagraph:
        """Return the largest quarter-point Paragraph that completely fits."""
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("replacement text must not be empty")
        available_width = width - (2 * self.options.horizontal_padding)
        available_height = height - (2 * self.options.vertical_padding)
        if available_width <= 0 or available_height <= 0:
            raise ValueError("bbox is too small after text padding")

        self._ensure_font(normalized)
        minimum = self._paragraph(normalized, self.options.min_font_size)
        minimum_width, minimum_height = self._natural_size(minimum, available_width)
        if minimum_width > available_width or minimum_height > available_height:
            raise ValueError(
                "replacement text cannot fit bbox at minimum font size "
                f"{self.options.min_font_size}: required {minimum_width:.2f}x{minimum_height:.2f}, "
                f"available {available_width:.2f}x{available_height:.2f}"
            )

        low = int(round(self.options.min_font_size * 4))
        high = int(round(self.options.max_font_size * 4))
        best: FittedParagraph | None = None
        while low <= high:
            middle = (low + high) // 2
            font_size = middle / 4
            paragraph = self._paragraph(normalized, font_size)
            natural_width, natural_height = self._natural_size(paragraph, available_width)
            if natural_width <= available_width and natural_height <= available_height:
                best = FittedParagraph(paragraph, font_size, natural_width, natural_height)
                low = middle + 1
            else:
                high = middle - 1
        if best is None:  # Defensive: min size was already checked above.
            raise ValueError("replacement text cannot fit bbox")
        return best

    def _paragraph(self, text: str, font_size: float):
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph

        alignment = {
            "left": TA_LEFT,
            "center": TA_CENTER,
            "right": TA_RIGHT,
            "justify": TA_JUSTIFY,
        }[self.options.alignment]
        style = ParagraphStyle(
            "pdf-craft-patch",
            fontName=self.options.font_name,
            fontSize=font_size,
            leading=font_size * self.options.line_height,
            alignment=cast(Any, alignment),
            wordWrap="CJK",
        )
        return Paragraph(escape(text), style)

    @staticmethod
    def _natural_size(paragraph, width: float) -> tuple[float, float]:
        # A very tall frame asks Platypus for the full natural height rather
        # than allowing a too-short frame to split and hide trailing content.
        return paragraph.wrap(width, 1_000_000)

    def _ensure_font(self, text: str) -> None:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont

        if self.options.font_name == "STSong-Light":
            try:
                pdfmetrics.getFont(self.options.font_name)
            except KeyError:
                pdfmetrics.registerFont(UnicodeCIDFont(self.options.font_name))
        try:
            pdfmetrics.getFont(self.options.font_name)
        except KeyError as error:
            raise ValueError(f"PDF patch font is unavailable: {self.options.font_name}") from error
        if any(ord(character) > 255 for character in text) and self.options.font_name in {
            "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
            "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
            "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
        }:
            raise ValueError(
                f"font {self.options.font_name} cannot reliably draw non-Latin replacement text"
            )

    def _validate_options(self) -> None:
        options = self.options
        if options.min_font_size <= 0 or options.max_font_size < options.min_font_size:
            raise ValueError("font sizes must be positive and max_font_size >= min_font_size")
        if options.line_height <= 0:
            raise ValueError("line_height must be positive")
        if options.horizontal_padding < 0 or options.vertical_padding < 0:
            raise ValueError("text padding must not be negative")
        if options.alignment not in {"left", "center", "right", "justify"}:
            raise ValueError(f"unsupported text alignment: {options.alignment}")
        if options.overflow not in {"error", "skip"}:
            raise ValueError(f"unsupported overflow policy: {options.overflow}")
