"""Paragraph flow backed by Qt's native ``QTextLayout`` engine."""
# pylint: disable=no-member,c-extension-no-member

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal

from .geometry import PageRectangle, region_in_page_points
from .models import PDFReplacement


Alignment = Literal["left", "center", "right", "justify"]
VerticalAlignment = Literal["top", "center", "bottom"]


@dataclass(frozen=True)
class PatchTextStyle:
    """Font and paragraph settings for one semantic ParagraphLayout level."""

    font_name: str = "Sans Serif"
    fallback_fonts: tuple[str, ...] = ()
    max_font_size: float = 12.0
    min_font_size: float = 4.0
    font_weight: int = 400
    line_height: float = 1.2
    horizontal_padding: float = 1.0
    vertical_padding: float = 1.0
    alignment: Alignment = "left"
    vertical_alignment: VerticalAlignment = "top"


@dataclass(frozen=True)
class PatchTextOptions:
    """Default style plus overrides keyed by ParagraphLayout semantic level.

    The scalar fields retain the previous one-style API. ``styles`` may use
    ``"text"`` / ``"sub_title"`` keys, or the more specific
    ``"sub_title:2"`` form. Missing fonts deliberately use Qt/system fallback.
    """

    font_name: str = "Sans Serif"
    fallback_fonts: tuple[str, ...] = ()
    max_font_size: float = 12.0
    min_font_size: float = 4.0
    font_weight: int = 400
    line_height: float = 1.2
    horizontal_padding: float = 1.0
    vertical_padding: float = 1.0
    alignment: Alignment = "left"
    vertical_alignment: VerticalAlignment = "top"
    styles: Mapping[str, PatchTextStyle] = field(default_factory=dict)
    overflow: Literal["error", "skip"] = "error"

    def style_for(self, layout_ref: str, layout_level: int) -> PatchTextStyle:
        """Resolve the most specific configured semantic text style."""
        default = PatchTextStyle(
            font_name=self.font_name,
            fallback_fonts=self.fallback_fonts,
            max_font_size=self.max_font_size,
            min_font_size=self.min_font_size,
            font_weight=self.font_weight,
            line_height=self.line_height,
            horizontal_padding=self.horizontal_padding,
            vertical_padding=self.vertical_padding,
            alignment=self.alignment,
            vertical_alignment=self.vertical_alignment,
        )
        return self.styles.get(f"{layout_ref}:{layout_level}", self.styles.get(layout_ref, default))


@dataclass(frozen=True)
class RegionTextPlacement:
    """One consecutive run of a paragraph assigned to a source rectangle."""

    page_index: int
    rectangle: PageRectangle
    remaining_text: str
    line_tops: tuple[float, ...]
    font_size: float
    style: PatchTextStyle


@dataclass(frozen=True)
class FittedParagraph:
    """A complete paragraph layout proven to fit its ordered source regions."""

    text: str
    font_size: float
    placements: tuple[RegionTextPlacement, ...]


class QTextParagraphFiller:
    """Lay one paragraph through ordered rectangles without splitting a line.

    Qt owns shaping, wrapping and glyph positioning. This class only decides
    each next full line's available rectangle and searches one uniform font
    size for the entire paragraph.
    """

    def __init__(self, options: PatchTextOptions | None = None) -> None:
        self.options = options or PatchTextOptions()

    def fit(
        self,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
    ) -> FittedParagraph:
        """Return the largest quarter-point paragraph fitting every region."""
        text = " ".join(replacement.text.split())
        if not text:
            raise ValueError("replacement text must not be empty")
        style = self.options.style_for(replacement.layout_ref, replacement.layout_level)
        self._validate_style(style)

        low = int(round(style.min_font_size * 4))
        high = int(round(style.max_font_size * 4))
        best: FittedParagraph | None = None
        while low <= high:
            middle = (low + high) // 2
            fitted = self._plan(text, replacement, page_sizes, style, middle / 4)
            if fitted is None:
                high = middle - 1
            else:
                best = fitted
                low = middle + 1
        if best is None:
            raise ValueError(
                "replacement text cannot fit paragraph source regions at minimum font size "
                f"{style.min_font_size}"
            )
        return best

    def draw_pdf_overlay(
        self,
        output_path: Path,
        page_size: tuple[float, float],
        placements: tuple[RegionTextPlacement, ...],
    ) -> None:
        """Write placements as a PDF text layer, never as a raster image."""
        if not placements:
            return
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        page_width, page_height = page_size
        writer = QtGui.QPdfWriter(str(output_path))
        writer.setResolution(72)
        writer.setPageSize(QtGui.QPageSize(
            QtCore.QSizeF(page_width, page_height), QtGui.QPageSize.Unit.Point,
        ))
        writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
        painter = QtGui.QPainter()
        if not painter.begin(writer):
            raise RuntimeError("Qt could not create the PDF text overlay")
        try:
            for placement in placements:
                self._draw_placement(QtCore, QtGui, painter, placement)
        finally:
            painter.end()

    def _plan(
        self,
        text: str,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        style: PatchTextStyle,
        font_size: float,
    ) -> FittedParagraph | None:
        remaining = text
        placements: list[RegionTextPlacement] = []
        for region in replacement.source_regions():
            if not remaining:
                break
            page_width, page_height = page_sizes[region.page_index]
            rectangle = region_in_page_points(region, page_width, page_height)
            placement, consumed = self._fit_region(
                region.page_index, rectangle, remaining, style, font_size,
            )
            if placement is None:
                continue
            placements.append(placement)
            remaining = remaining[consumed:]
        if remaining:
            return None
        return FittedParagraph(text, font_size, tuple(placements))

    def _fit_region(
        self,
        page_index: int,
        rectangle: PageRectangle,
        text: str,
        style: PatchTextStyle,
        font_size: float,
    ) -> tuple[RegionTextPlacement | None, int]:
        available_width = rectangle.width - 2 * style.horizontal_padding
        available_top = rectangle.top + style.vertical_padding
        available_bottom = rectangle.bottom - style.vertical_padding
        if available_width <= 0 or available_bottom <= available_top:
            return None, 0

        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        layout = self._create_layout(QtCore, QtGui, text, style, font_size)
        line_tops: list[float] = []
        consumed_utf16 = 0
        y = available_top
        last_line_height = 0.0
        layout.beginLayout()
        try:
            while True:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(available_width)
                line_height = line.height()
                if y + line_height > available_bottom + 1e-6:
                    break
                line_tops.append(y)
                consumed_utf16 = line.textStart() + line.textLength()
                last_line_height = line_height
                y += line_height * style.line_height
        finally:
            layout.endLayout()
        if not line_tops or consumed_utf16 <= 0:
            return None, 0

        content_height = y - available_top - last_line_height * (style.line_height - 1)
        spare = max(available_bottom - available_top - content_height, 0.0)
        if style.vertical_alignment == "center":
            shift = spare / 2
        elif style.vertical_alignment == "bottom":
            shift = spare
        else:
            shift = 0.0
        return RegionTextPlacement(
            page_index,
            rectangle,
            text,
            tuple(top + shift for top in line_tops),
            font_size,
            style,
        ), _python_index_for_utf16(text, consumed_utf16)

    def _draw_placement(self, QtCore, QtGui, painter, placement: RegionTextPlacement) -> None:
        layout = self._create_layout(
            QtCore, QtGui, placement.remaining_text, placement.style, placement.font_size,
        )
        available_width = placement.rectangle.width - 2 * placement.style.horizontal_padding
        x = placement.rectangle.x + placement.style.horizontal_padding
        layout.beginLayout()
        try:
            for top in placement.line_tops:
                line = layout.createLine()
                if not line.isValid():
                    raise RuntimeError("Qt layout changed while drawing a planned paragraph")
                line.setLineWidth(available_width)
                line.setPosition(QtCore.QPointF(x, top))
        finally:
            layout.endLayout()
        layout.draw(painter, QtCore.QPointF(0, 0))

    @staticmethod
    def _create_layout(QtCore, QtGui, text: str, style: PatchTextStyle, font_size: float):
        font = QtGui.QFont(style.font_name)
        if style.fallback_fonts:
            font.setFamilies([style.font_name, *style.fallback_fonts])
        font.setPointSizeF(font_size)
        font.setWeight(QtGui.QFont.Weight(style.font_weight))
        option = QtGui.QTextOption()
        option.setWrapMode(QtGui.QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment({
            "left": QtCore.Qt.AlignmentFlag.AlignLeft,
            "center": QtCore.Qt.AlignmentFlag.AlignHCenter,
            "right": QtCore.Qt.AlignmentFlag.AlignRight,
            "justify": QtCore.Qt.AlignmentFlag.AlignJustify,
        }[style.alignment])
        layout = QtGui.QTextLayout(text, font)
        layout.setTextOption(option)
        return layout

    @staticmethod
    def _validate_style(style: PatchTextStyle) -> None:
        if style.min_font_size <= 0 or style.max_font_size < style.min_font_size:
            raise ValueError("font sizes must be positive and max_font_size >= min_font_size")
        if style.line_height <= 0:
            raise ValueError("line_height must be positive")
        if style.horizontal_padding < 0 or style.vertical_padding < 0:
            raise ValueError("text padding must be non-negative")
        if style.alignment not in {"left", "center", "right", "justify"}:
            raise ValueError(f"unsupported text alignment: {style.alignment}")
        if style.vertical_alignment not in {"top", "center", "bottom"}:
            raise ValueError(f"unsupported vertical alignment: {style.vertical_alignment}")


def _python_index_for_utf16(text: str, utf16_index: int) -> int:
    """Translate Qt's UTF-16 text position to a Python string index."""
    units = 0
    for index, character in enumerate(text):
        units += 2 if ord(character) > 0xFFFF else 1
        if units >= utf16_index:
            return index + 1
    return len(text)


_QT_APPLICATION = None


def _qt_modules():
    try:
        from PySide6 import QtCore, QtGui
    except ImportError as error:  # pragma: no cover - dependency metadata covers this.
        raise RuntimeError(
            "PDF paragraph filling requires the PySide6/Qt runtime; install pdf-craft with its dependencies"
        ) from error
    return QtCore, QtGui


def _ensure_qt_application(QtGui) -> None:
    """Initialise Qt's font database for headless library use exactly once."""
    global _QT_APPLICATION  # pylint: disable=global-statement
    if QtGui.QGuiApplication.instance() is not None:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _QT_APPLICATION = QtGui.QGuiApplication([])
