"""Paragraph flow backed by Qt's native ``QTextLayout`` engine."""
# pylint: disable=no-member,c-extension-no-member

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
import os
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .geometry import PageRectangle, region_in_page_points
from .models import PDFReplacement, PDFReplacementRegion


Alignment = Literal["left", "center", "right", "justify"]
VerticalAlignment = Literal["top", "center", "bottom"]
FontResolutionSource = Literal["automatic", "configured", "qt-fallback"]


_FONT_SIZE_TOLERANCE = 0.05
_MAX_FONT_SIZE_SEARCH_ITERATIONS = 16
_CJK_FONT_CANDIDATES = (
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "Hiragino Sans GB",
    "Heiti SC",
    "Arial Unicode MS",
)
_GENERAL_FONT_CANDIDATES = (
    "Arial",
    "Helvetica",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
)


@dataclass(frozen=True)
class PatchTextStyle:
    """Font and paragraph settings for one semantic ParagraphLayout level."""

    # ``None`` (or an empty string supplied by a caller) asks the filler to
    # select one installed family once for the entire patch run.
    font_name: str | None = None
    fallback_fonts: tuple[str, ...] = ()
    max_font_size: float = 12.0
    min_font_size: float = 4.0
    font_weight: int = 400
    line_height: float = 1.2
    horizontal_padding: float = 1.0
    vertical_padding: float = 1.0
    alignment: Alignment = "left"
    vertical_alignment: VerticalAlignment = "top"
    minimum_body_font_ratio: float | None = None


@dataclass(frozen=True)
class PatchTextOptions:
    """Default style plus overrides keyed by ParagraphLayout semantic level.

    The scalar fields retain the previous one-style API. ``styles`` may use
    ``"text"`` / ``"sub_title"`` keys, or the more specific
    ``"sub_title:2"`` form. An unspecified ``font_name`` selects an installed
    local family once per patch run; explicit missing fonts use Qt fallback.
    """

    font_name: str | None = None
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
    headline_min_body_ratio: float = 1.2
    headline_fallback_font_size: float | None = None
    overflow: Literal["error", "skip"] = "error"

    def style_for(self, layout_ref: str, layout_level: int) -> PatchTextStyle:
        """Resolve the most specific configured semantic text style.

        An implicit headline style reserves enough of the scalar default range
        to meet the default body-relative minimum.  Explicit headline styles
        remain hard user limits and are therefore returned unchanged.
        """
        specific = self.styles.get(f"{layout_ref}:{layout_level}")
        if specific is not None:
            return specific
        generic = self.styles.get(layout_ref)
        if generic is not None:
            return generic
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
        if layout_ref == "sub_title":
            minimum_maximum = self.max_font_size * self.headline_min_body_ratio
            return PatchTextStyle(
                font_name=default.font_name,
                fallback_fonts=default.fallback_fonts,
                max_font_size=max(default.max_font_size, minimum_maximum),
                min_font_size=default.min_font_size,
                font_weight=default.font_weight,
                line_height=default.line_height,
                horizontal_padding=default.horizontal_padding,
                vertical_padding=default.vertical_padding,
                alignment=default.alignment,
                vertical_alignment=default.vertical_alignment,
            )
        return default

    def headline_ratio_for(self, layout_ref: str, layout_level: int) -> float:
        """Return the configured lower-bound ratio for one headline level."""
        style = self.style_for(layout_ref, layout_level)
        ratio = style.minimum_body_font_ratio
        if ratio is None:
            ratio = self.headline_min_body_ratio
        if ratio <= 0:
            raise ValueError("headline minimum body font ratio must be positive")
        return ratio

@dataclass(frozen=True)
class RegionTextPlacement:
    """One consecutive run of a paragraph assigned to a source rectangle."""

    page_index: int
    rectangle: PageRectangle
    remaining_text: str
    # These are effective glyph bounds after QTextLayout applies alignment.
    # They make the otherwise opaque Qt placement decision inspectable in
    # deterministic tests; drawing still lets Qt perform the shaping.
    line_text_lefts: tuple[float, ...]
    line_text_widths: tuple[float, ...]
    line_tops: tuple[float, ...]
    line_heights: tuple[float, ...]
    font_size: float
    style: PatchTextStyle


@dataclass(frozen=True)
class FittedParagraph:
    """A complete paragraph layout proven to fit its ordered source regions."""

    text: str
    font_size: float
    placements: tuple[RegionTextPlacement, ...]


@dataclass(frozen=True)
class PlannedParagraph:
    """One replacement and its finished paragraph-level fill plan."""

    replacement: PDFReplacement
    paragraph: FittedParagraph


@dataclass(frozen=True)
class _PlannedParagraphSummary:
    """The small, paragraph-wide state retained after placements are spooled."""

    replacement: PDFReplacement
    text: str
    font_size: float


@dataclass(frozen=True)
class _PagePlanContribution:
    """One paragraph's erase and text work for one output page."""

    paragraph_index: int
    regions: tuple[PDFReplacementRegion, ...]
    placements: tuple[RegionTextPlacement, ...]


class _WindowPlanStorage:
    """Append serialized page contributions while a window is being measured."""

    def __init__(self) -> None:
        self._temporary_directory = TemporaryDirectory(prefix="pdf-craft-layout-")
        self._root = Path(self._temporary_directory.name)
        self._page_plan_paths: dict[int, Path] = {}

    def append(
        self,
        paragraph_index: int,
        replacement: PDFReplacement,
        paragraph: FittedParagraph,
    ) -> None:
        regions_by_page: dict[int, list[PDFReplacementRegion]] = {}
        for region in replacement.source_regions():
            regions_by_page.setdefault(region.page_index, []).append(region)
        placements_by_page: dict[int, list[RegionTextPlacement]] = {}
        for placement in paragraph.placements:
            placements_by_page.setdefault(placement.page_index, []).append(placement)
        for page_index in sorted(regions_by_page | placements_by_page):
            contribution = _PagePlanContribution(
                paragraph_index,
                tuple(regions_by_page.get(page_index, ())),
                tuple(placements_by_page.get(page_index, ())),
            )
            path = self._page_plan_paths.setdefault(
                page_index, self._root / f"page-{page_index}.pickle",
            )
            with path.open("ab") as stream:
                pickle.dump(contribution, stream, protocol=pickle.HIGHEST_PROTOCOL)

    def into_plan(
        self,
        first_page_index: int,
        last_page_index: int,
        summaries: tuple[_PlannedParagraphSummary, ...],
    ) -> "FillWindowPlan":
        return FillWindowPlan(
            first_page_index,
            last_page_index,
            summaries,
            self._page_plan_paths,
            self._temporary_directory,
        )

    def close(self) -> None:
        self._temporary_directory.cleanup()


@dataclass
class FillWindowPlan:
    """A contiguous, releasable set of pages planned in two layout phases.

    Detailed placements are serialized by page rather than retained in the
    window object. The PDF composer can therefore load one page's work, render
    it, and release it without coupling typography to erasure or retaining a
    whole book of page rasters or a very long paragraph's glyph coordinates.
    """

    first_page_index: int
    last_page_index: int
    _summaries: tuple[_PlannedParagraphSummary, ...]
    _page_plan_paths: Mapping[int, Path]
    _temporary_directory: TemporaryDirectory

    @property
    def has_page_contributions(self) -> bool:
        """Whether this window has any page work to compose."""
        return bool(self._page_plan_paths)

    def page_contributions(self, page_index: int) -> Iterator[_PagePlanContribution]:
        """Yield the serialized text and erase work for one page only."""
        try:
            path = self._page_plan_paths[page_index]
        except KeyError:
            return
        with path.open("rb") as stream:
            while True:
                try:
                    yield pickle.load(stream)
                except EOFError:
                    return

    @property
    def paragraphs(self) -> tuple[PlannedParagraph, ...]:
        """Materialize all placements for compatibility with plan inspection.

        Production composition deliberately uses :meth:`page_contributions`.
        Callers that inspect this compatibility view explicitly opt into the
        corresponding whole-window allocation.
        """
        placements = [[] for _ in self._summaries]
        for page_index in range(self.first_page_index, self.last_page_index + 1):
            for contribution in self.page_contributions(page_index):
                placements[contribution.paragraph_index].extend(contribution.placements)
        return tuple(
            PlannedParagraph(
                summary.replacement,
                FittedParagraph(summary.text, summary.font_size, tuple(placements[index])),
            )
            for index, summary in enumerate(self._summaries)
        )

    def close(self) -> None:
        """Release serialized placement files once the window has been composed."""
        self._temporary_directory.cleanup()


class HeadlineConstraintError(ValueError):
    """A headline cannot satisfy its configured body-relative font lower bound."""

    def __init__(self, replacement: PDFReplacement, minimum_font_size: float, cause: ValueError) -> None:
        self.replacement = replacement
        self.minimum_font_size = minimum_font_size
        self.__cause__ = cause
        super().__init__(
            f"page {replacement.page_index}, bbox {replacement.bbox}: "
            "headline cannot satisfy body-relative minimum font size "
            f"{minimum_font_size:.2f}: {cause}"
        )


@dataclass(frozen=True)
class FontResolution:
    """One requested family and its installed first-choice diagnostic family.

    ``source`` distinguishes automatic selection from an explicit family and
    from Qt's fallback for an unavailable explicit family.  A fallback result
    is diagnostic only: Qt may still select another family for individual
    missing glyphs while shaping text.
    """

    requested_font_name: str | None
    resolved_font_name: str
    source: FontResolutionSource


class QTextParagraphFiller:
    """Lay one paragraph through ordered rectangles without splitting a line.

    Qt owns shaping, wrapping and glyph positioning. This class only decides
    each next full line's available rectangle and searches one uniform font
    size for the entire paragraph.
    """

    def __init__(self, options: PatchTextOptions | None = None) -> None:
        self.options = options or PatchTextOptions()
        self._auto_font_resolution: FontResolution | None = None
        self._font_resolutions: dict[str | None, FontResolution] = {}

    @property
    def font_resolutions(self) -> tuple[FontResolution, ...]:
        """Resolved font diagnostics accumulated during the current patch run."""
        return tuple(self._font_resolutions.values())

    def reset_font_resolutions(self) -> None:
        """Start a new patch run without retaining its previous font choice."""
        self._auto_font_resolution = None
        self._font_resolutions.clear()

    def fit(
        self,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        minimum_font_size: float | None = None,
    ) -> FittedParagraph:
        """Return the largest verified paragraph size fitting every region.

        QTextLayout accepts floating-point point sizes.  Keep the successful
        side of a bounded binary search so a discontinuity at a wrapping
        threshold cannot produce an overflowing result.
        """
        text = " ".join(replacement.text.split())
        if not text:
            raise ValueError("replacement text must not be empty")
        style = self.options.style_for(replacement.layout_ref, replacement.layout_level)
        if (
            replacement.layout_ref == "sub_title"
            and not _has_explicit_style(
                self.options,
                replacement.layout_ref, replacement.layout_level,
            )
            and minimum_font_size is not None
        ):
            style = replace(
                style,
                max_font_size=max(style.max_font_size, minimum_font_size),
            )
        style = self._resolve_font(style, text)
        self._validate_style(style)

        effective_minimum = max(style.min_font_size, minimum_font_size or style.min_font_size)
        if effective_minimum > style.max_font_size:
            raise ValueError(
                f"requested minimum font size {effective_minimum:.2f} exceeds style maximum "
                f"{style.max_font_size:.2f}"
            )
        lower = effective_minimum
        best = self._plan(text, replacement, page_sizes, style, lower)
        if best is None:
            raise ValueError(
                "replacement text cannot fit paragraph source regions at minimum font size "
                f"{effective_minimum}"
            )

        upper = style.max_font_size
        fitted_at_upper = self._plan(text, replacement, page_sizes, style, upper)
        if fitted_at_upper is not None:
            return fitted_at_upper

        for _ in range(_MAX_FONT_SIZE_SEARCH_ITERATIONS):
            if upper - lower <= _FONT_SIZE_TOLERANCE:
                break
            middle = (lower + upper) / 2
            fitted = self._plan(text, replacement, page_sizes, style, middle)
            if fitted is None:
                upper = middle
            else:
                best = fitted
                lower = middle
        return best

    def _resolve_font(self, style: PatchTextStyle, text: str) -> PatchTextStyle:
        """Resolve an automatic family once, without rejecting explicit names."""
        requested = style.font_name.strip() if style.font_name else None
        QtCore, QtGui = _qt_modules()
        del QtCore
        _ensure_qt_application(QtGui)
        families = _font_database_families(QtGui)
        if requested is None:
            resolution = self._auto_font_resolution
            if resolution is None:
                resolution = FontResolution(
                    None,
                    _choose_automatic_font(
                        families, _system_font_family(QtGui), _contains_cjk(text),
                    ),
                    "automatic",
                )
                self._auto_font_resolution = resolution
                self._font_resolutions[None] = resolution
            return replace(style, font_name=resolution.resolved_font_name)

        if requested not in self._font_resolutions:
            installed = _matching_font_family(families, requested)
            self._font_resolutions[requested] = FontResolution(
                requested,
                installed or _choose_automatic_font(
                    families, _system_font_family(QtGui), _contains_cjk(text),
                ),
                "configured" if installed else "qt-fallback",
            )
        # Preserve an explicit request so Qt can apply its ordinary per-glyph
        # fallback chain rather than forcing a single diagnostic family.
        return replace(style, font_name=requested)

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
        content_left = rectangle.x + style.horizontal_padding
        line_tops: list[float] = []
        line_text_lefts: list[float] = []
        line_text_widths: list[float] = []
        line_heights: list[float] = []
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
                line_start = _x_coordinate(line.cursorToX(0))
                line_end = _x_coordinate(line.cursorToX(line.textLength()))
                line_text_lefts.append(content_left + line_start)
                line_text_widths.append(line_end - line_start)
                line_heights.append(line_height)
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
            tuple(line_text_lefts),
            tuple(line_text_widths),
            tuple(top + shift for top in line_tops),
            tuple(line_heights),
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
        families = tuple(
            family for family in (style.font_name, *style.fallback_fonts) if family
        )
        font = QtGui.QFont(families[0]) if families else QtGui.QFont()
        if len(families) > 1:
            font.setFamilies(list(families))
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


class WindowedParagraphPlanner:
    """Plan body text before headlines while retaining only a page window.

    Input replacements must be in reading order by their first source page.
    A window closes as soon as no future paragraph can touch one of its pages;
    this keeps page rasters and transient Qt layouts out of the book-wide
    working set. Paragraphs themselves still retain one uniform font size over
    every source region, including regions on later pages.
    """

    def __init__(
        self,
        filler: QTextParagraphFiller,
        page_sizes: dict[int, tuple[float, float]],
        options: PatchTextOptions | None = None,
    ) -> None:
        self._filler = filler
        self._page_sizes = page_sizes
        self._options = options or filler.options
        self.skipped: list[tuple[PDFReplacement, ValueError]] = []
        self._previous_body_font_size: float | None = None

    def plan(self, replacements: Iterable[PDFReplacement]) -> Iterator[FillWindowPlan]:
        """Yield completed windows in page order without retaining the full book."""
        window: list[PDFReplacement] = []
        first_page: int | None = None
        last_page = 0
        previous_first_page = 0
        for replacement in replacements:
            replacement_first, replacement_last = _replacement_page_span(replacement)
            if replacement_first < previous_first_page:
                raise ValueError("PDF replacements must be ordered by first source page")
            previous_first_page = replacement_first
            if window and replacement_first > last_page:
                assert first_page is not None
                yield self._plan_window(first_page, last_page, window)
                window = []
                first_page = None
                last_page = 0
            if first_page is None:
                first_page = replacement_first
            window.append(replacement)
            last_page = max(last_page, replacement_last)
        if window:
            assert first_page is not None
            yield self._plan_window(first_page, last_page, window)

    def _plan_window(
        self,
        first_page: int,
        last_page: int,
        replacements: list[PDFReplacement],
    ) -> FillWindowPlan:
        body = [replacement for replacement in replacements if not _is_headline(replacement)]
        headlines = [replacement for replacement in replacements if _is_headline(replacement)]
        summaries: list[_PlannedParagraphSummary] = []
        body_font_sizes: dict[int, float] = {}
        storage = _WindowPlanStorage()

        try:
            for replacement in body:
                paragraph = self._fit_or_skip(replacement)
                if paragraph is None:
                    continue
                paragraph_index = len(summaries)
                summaries.append(_PlannedParagraphSummary(
                    replacement, paragraph.text, paragraph.font_size,
                ))
                for placement in paragraph.placements:
                    body_font_sizes[placement.page_index] = max(
                        body_font_sizes.get(placement.page_index, 0.0), paragraph.font_size,
                    )
                storage.append(paragraph_index, replacement, paragraph)

            for replacement in headlines:
                minimum = self._headline_minimum(replacement, body_font_sizes)
                try:
                    paragraph = self._filler.fit(replacement, self._page_sizes, minimum)
                except ValueError as error:
                    constraint = HeadlineConstraintError(replacement, minimum, error)
                    if self._options.overflow == "skip":
                        self.skipped.append((replacement, constraint))
                        continue
                    raise constraint from error
                paragraph_index = len(summaries)
                summaries.append(_PlannedParagraphSummary(
                    replacement, paragraph.text, paragraph.font_size,
                ))
                storage.append(paragraph_index, replacement, paragraph)

            if body_font_sizes:
                latest_page = max(body_font_sizes)
                self._previous_body_font_size = body_font_sizes[latest_page]
            return storage.into_plan(first_page, last_page, tuple(summaries))
        except Exception:
            storage.close()
            raise

    def _fit_or_skip(self, replacement: PDFReplacement) -> FittedParagraph | None:
        try:
            return self._filler.fit(replacement, self._page_sizes)
        except ValueError as error:
            if self._options.overflow == "skip":
                self.skipped.append((replacement, error))
                return None
            raise _replacement_error(replacement, error) from error

    def _headline_minimum(
        self,
        replacement: PDFReplacement,
        body_font_sizes: Mapping[int, float],
    ) -> float:
        references = [
            reference
            for page_index in _replacement_pages(replacement)
            if (reference := self._body_reference_for_page(page_index, body_font_sizes)) is not None
        ]
        reference = max(references) if references else None
        style = self._options.style_for(replacement.layout_ref, replacement.layout_level)
        if reference is None:
            return max(style.min_font_size, self._options.headline_fallback_font_size or 0.0)
        return max(
            style.min_font_size,
            reference * self._options.headline_ratio_for(
                replacement.layout_ref, replacement.layout_level,
            ),
        )

    def _body_reference_for_page(
        self,
        page_index: int,
        body_font_sizes: Mapping[int, float],
    ) -> float | None:
        if page_index in body_font_sizes:
            return body_font_sizes[page_index]
        preceding = [page for page in body_font_sizes if page < page_index]
        if preceding:
            return body_font_sizes[max(preceding)]
        if self._previous_body_font_size is not None:
            return self._previous_body_font_size
        following = [page for page in body_font_sizes if page > page_index]
        if following:
            return body_font_sizes[min(following)]
        return None


def _is_headline(replacement: PDFReplacement) -> bool:
    """Use semantic chapter metadata, never image appearance, for title roles."""
    return replacement.layout_ref == "sub_title"


def _has_explicit_style(
    options: PatchTextOptions,
    layout_ref: str,
    layout_level: int,
) -> bool:
    """Whether a semantic style supplies a deliberate user font ceiling."""
    return (
        f"{layout_ref}:{layout_level}" in options.styles
        or layout_ref in options.styles
    )


def _replacement_pages(replacement: PDFReplacement) -> tuple[int, ...]:
    return tuple(sorted({region.page_index for region in replacement.source_regions()}))


def _replacement_page_span(replacement: PDFReplacement) -> tuple[int, int]:
    pages = _replacement_pages(replacement)
    if not pages:  # source_regions always provides a legacy region, defensive only.
        raise ValueError("PDF replacement has no source regions")
    return pages[0], pages[-1]


def _replacement_error(replacement: PDFReplacement, error: ValueError) -> ValueError:
    """Give deferred streaming layout failures the old patcher context."""
    return ValueError(f"page {replacement.page_index}, bbox {replacement.bbox}: {error}")


def _python_index_for_utf16(text: str, utf16_index: int) -> int:
    """Translate Qt's UTF-16 text position to a Python string index."""
    units = 0
    for index, character in enumerate(text):
        units += 2 if ord(character) > 0xFFFF else 1
        if units >= utf16_index:
            return index + 1
    return len(text)


def _x_coordinate(cursor_position) -> float:
    """Return PySide's x component from ``QTextLine.cursorToX``.

    PySide exposes ``cursorToX`` as ``(x, edge)`` while some Qt bindings use a
    scalar. Keeping the conversion here makes the layout plan portable across
    supported PySide versions.
    """
    if isinstance(cursor_position, tuple):
        return float(cursor_position[0])
    return float(cursor_position)


def _font_database_families(QtGui) -> tuple[str, ...]:
    """Return the actual families exposed by the active Qt font database."""
    return tuple(QtGui.QFontDatabase.families())


def _matching_font_family(families: Iterable[str], requested: str) -> str | None:
    """Return Qt's canonical spelling of ``requested`` when it is installed."""
    normalized = requested.casefold()
    return next((family for family in families if family.casefold() == normalized), None)


def _contains_cjk(text: str) -> bool:
    """Whether text contains Han, Kana, or Hangul that merits CJK candidates."""
    return any(
        "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\U00020000" <= character <= "\U0002ebef"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
        for character in text
    )


def _choose_automatic_font(
    families: tuple[str, ...],
    system_font_family: str | None,
    contains_cjk: bool,
) -> str:
    """Choose an installed family, preferring known CJK families when needed."""
    if contains_cjk:
        for candidate in _CJK_FONT_CANDIDATES:
            if matched := _matching_font_family(families, candidate):
                return matched
    if system_font_family and (matched := _matching_font_family(families, system_font_family)):
        return matched
    for candidate in _GENERAL_FONT_CANDIDATES:
        if matched := _matching_font_family(families, candidate):
            return matched
    if families:
        return families[0]
    raise RuntimeError("Qt reported no installed font families for PDF paragraph filling")


def _system_font_family(QtGui) -> str | None:
    """Read Qt's application default after its font database has initialized."""
    application = QtGui.QGuiApplication.instance()
    if application is None:  # Defensive: callers initialize it before resolving.
        return None
    return application.font().family() or None


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
