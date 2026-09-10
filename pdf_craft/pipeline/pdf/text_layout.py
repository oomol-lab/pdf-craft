"""Paragraph flow backed by Qt's native ``QTextLayout`` engine."""
# pylint: disable=no-member,c-extension-no-member

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from math import isfinite
import os
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .geometry import PageRectangle, region_in_page_points
from .models import PDFReplacement, PDFReplacementRegion
from .inline_formula import FormulaFragment, InlineFormulaPDFRenderer
from pdf_craft.formula import latex_to_plain_text


Alignment = Literal["left", "center", "right", "justify"]
VerticalAlignment = Literal["top", "center", "bottom"]
FontResolutionSource = Literal["automatic", "configured", "qt-fallback"]


_FONT_SIZE_TOLERANCE = 0.05
_MAX_FONT_SIZE_SEARCH_ITERATIONS = 16
_AUTOMATIC_FONT_SIZE_INITIAL = 12.0
_MAX_AUTOMATIC_FONT_SIZE_BRACKETING_ITERATIONS = 16
_HEADLINE_OVERFLOW_LINE_WIDTH = 1_000_000.0
# QTextLayout otherwise measures against the GUI screen's low-DPI font grid.
# Keep all layout geometry in a larger private coordinate system, then scale
# it back at PDF draw time.  This preserves Qt's shaping and break decisions
# while making a 0.05pt search a real typographic search rather than a screen
# pixel search.
_LAYOUT_SCALE = 8.0
_SLOT_PLAN_BEAM_WIDTH = 64
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
    # ``None`` leaves the visual size to geometry-driven fitting.  The fitter
    # grows a bounded search interval from an internal initial probe; a numeric
    # value is an explicit hard ceiling.
    max_font_size: float | None = None
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
    An unspecified ``max_font_size`` is an automatic fit, while a numeric
    value is a hard ceiling for the affected semantic style.
    """

    font_name: str | None = None
    fallback_fonts: tuple[str, ...] = ()
    max_font_size: float | None = None
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
    render_inline_formulas: bool = True

    def style_for(self, layout_ref: str, layout_level: int) -> PatchTextStyle:
        """Resolve the most specific configured semantic text style.

        A numeric option-level maximum applies unchanged to every implicit
        semantic style, including headlines.  Headline/body ratios are lower
        bounds used when space permits; they must never silently enlarge a
        user-provided ceiling.
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
    allows_horizontal_overflow: bool = False
    formula_draws: tuple["FormulaDraw", ...] = ()
    # The exact text consumed by this region during the paragraph-wide first
    # pass.  ``remaining_text`` is retained for compatibility, but a later
    # local optimisation must never reflow text into a neighbouring region.
    assigned_text: str = ""
    # ``rectangle.bottom`` is a visual target, not the hard edge.  This is the
    # first lower overlapping source region (or the page bottom) that text
    # must not enter.
    forbidden_bottom: float | None = None


@dataclass(frozen=True)
class FormulaDraw:
    """A vector formula anchored to the baseline chosen by QTextLayout."""

    pdf: bytes
    x: float
    baseline: float
    descent: float
    # Keep enough source information to re-measure the atom during the local
    # second pass.  The proxy location is expressed in Python string indexes
    # of this placement's assigned text.
    latex: str = ""
    proxy_start: int = 0
    proxy_length: int = 0
    # Formula PDFs remain in ordinary PDF points; ``scale`` keeps the merger
    # compatible with any future non-unit fragment coordinate system.
    scale: float = 1.0


@dataclass(frozen=True)
class _FormulaSpan:
    start: int
    length: int
    fragment: FormulaFragment
    latex: str


@dataclass(frozen=True)
class _RegionSlotDecision:
    """A constant-size choice for one physical source region.

    This deliberately describes *capacity*, not a materialized list of rows.
    QTextLine objects are only created once a complete decision plan is being
    tried against the actual paragraph text.
    """

    page_index: int
    rectangle: PageRectangle
    line_count: int
    delta_to_aim: float
    vertical_offset: float
    top_boundary: float
    bottom_boundary: float
    choice: Literal["loose", "tight"]


@dataclass(frozen=True)
class _SlotDecisionPlan:
    """One bounded, whole-paragraph combination of region decisions."""

    decisions: tuple[_RegionSlotDecision, ...]
    signed_delta: float


@dataclass(frozen=True)
class FittedParagraph:
    """A paragraph flow plan proven to fit its ordered source regions.

    ``font_size`` is the uniform first-pass size.  Its frozen placements may
    subsequently carry independently normalized local sizes.
    """

    text: str
    font_size: float
    placements: tuple[RegionTextPlacement, ...]
    # A multi-bbox plan may consume the text before it reaches every source
    # region.  Retain this compact diagnostic so font-size selection does not
    # mistake an empty continuation region for a perfectly fitted paragraph.
    unused_region_count: int = 0
    unused_slot_count: int = 0


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

    def rewrite_placements(
        self,
        transform: Callable[[int, RegionTextPlacement], RegionTextPlacement],
    ) -> None:
        """Replace placements one serialized page at a time.

        A paragraph can stretch across a large document, so second-pass
        typography must not first materialize all of its glyph coordinates in
        a window-wide list.  Each page stream is read, transformed, and
        atomically replaced before moving to the next one.
        """
        for path in self._page_plan_paths.values():
            temporary_path = path.with_suffix(".updated")
            with path.open("rb") as source, temporary_path.open("wb") as target:
                while True:
                    try:
                        contribution = pickle.load(source)
                    except EOFError:
                        break
                    rewritten = _PagePlanContribution(
                        contribution.paragraph_index,
                        contribution.regions,
                        tuple(
                            transform(contribution.paragraph_index, placement)
                            for placement in contribution.placements
                        ),
                    )
                    pickle.dump(rewritten, target, protocol=pickle.HIGHEST_PROTOCOL)
            temporary_path.replace(path)

    def page_contributions(self, page_index: int) -> Iterator[_PagePlanContribution]:
        """Read one spooled page while retaining no other page's placements."""
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
    def page_indexes(self) -> tuple[int, ...]:
        """The small index of pages that currently have serialized work."""
        return tuple(self._page_plan_paths)

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
        self._formula_renderer = InlineFormulaPDFRenderer()
        self._layout_obstacles: Mapping[int, tuple[PageRectangle, ...]] = {}
        self._uses_window_obstacles = False
        self._active_forbidden_bottom: float | None = None

    @property
    def font_resolutions(self) -> tuple[FontResolution, ...]:
        """Resolved font diagnostics accumulated during the current patch run."""
        return tuple(self._font_resolutions.values())

    def reset_font_resolutions(self) -> None:
        """Start a new patch run without retaining its previous font choice."""
        self._auto_font_resolution = None
        self._font_resolutions.clear()

    def set_layout_obstacles(
        self, obstacles: Mapping[int, tuple[PageRectangle, ...]],
    ) -> None:
        """Set the current window's immutable source-geometry obstacle map."""
        self._layout_obstacles = obstacles
        self._uses_window_obstacles = True

    def prepare_automatic_font(self, contains_cjk: bool) -> None:
        """Fix this run's automatic family before its first paragraph is fitted.

        The patcher determines ``contains_cjk`` from every replacement that
        uses an unspecified style.  Direct ``fit`` callers retain lazy
        selection from their own text because they do not supply a run.
        """
        if self._auto_font_resolution is not None:
            return
        QtCore, QtGui = _qt_modules()
        del QtCore
        _ensure_qt_application(QtGui)
        resolution = FontResolution(
            None,
            _choose_automatic_font(
                _font_database_families(QtGui), _system_font_family(QtGui), contains_cjk,
            ),
            "automatic",
        )
        self._auto_font_resolution = resolution
        self._font_resolutions[None] = resolution

    def fit(
        self,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        minimum_font_size: float | None = None,
    ) -> FittedParagraph:
        """Return a verified paragraph whose terminal line approaches its aim.

        A source bbox bottom is an aim line.  A lower overlapping layout box
        (or the page bottom) is the actual forbidden line.  Qt continues to
        own text shaping and line breaking; this method only searches sizes
        that fully fit before the forbidden line.
        """
        # Preserve the translated character stream verbatim.  QTextLayout is
        # responsible for interpreting its whitespace and native line-break
        # opportunities; the formula-object proxy built below is the sole
        # intentional text-level substitution.
        text = replacement.text
        font_text = self._materialize_formula_fallbacks(replacement)
        if not text:
            raise ValueError("replacement text must not be empty")
        style = self.options.style_for(replacement.layout_ref, replacement.layout_level)
        style = self._resolve_font(style, font_text)
        self._validate_style(style)

        effective_minimum = max(style.min_font_size, minimum_font_size or style.min_font_size)
        configured_maximum = style.max_font_size
        if configured_maximum is not None and effective_minimum > configured_maximum:
            raise ValueError(
                f"requested minimum font size {effective_minimum:.2f} exceeds style maximum "
                f"{configured_maximum:.2f}"
            )
        previous_obstacles = self._layout_obstacles
        if not self._uses_window_obstacles:
            self._layout_obstacles = _replacement_obstacles(replacement, page_sizes)
        try:
            lower = effective_minimum
            minimum_plan = self._plan(text, replacement, page_sizes, style, lower)
            if minimum_plan is None:
                raise ValueError(
                    "replacement text cannot fit paragraph source regions at minimum font size "
                    f"{effective_minimum}"
                )

            upper = configured_maximum
            if upper is None:
                upper = self._find_automatic_font_size_upper_bound(
                    text, replacement, page_sizes, style, lower,
                )
            maximum_plan = self._plan(text, replacement, page_sizes, style, upper)
            if maximum_plan is not None:
                best = maximum_plan
                feasible_lower = upper
            else:
                best = minimum_plan
                feasible_lower = lower
                infeasible_upper = upper
                for _ in range(_MAX_FONT_SIZE_SEARCH_ITERATIONS):
                    if infeasible_upper - feasible_lower <= _FONT_SIZE_TOLERANCE:
                        break
                    middle = (feasible_lower + infeasible_upper) / 2
                    fitted = self._plan(text, replacement, page_sizes, style, middle)
                    if fitted is None:
                        infeasible_upper = middle
                    else:
                        best = fitted
                        feasible_lower = middle

            # Test doubles and external subclasses may report a successful
            # paragraph without physical placements.  They have no aim line,
            # so retain the historical successful-side binary-search result.
            if not minimum_plan.placements or not best.placements:
                return best

            if len(replacement.source_regions()) > 1:
                # Qt can switch the terminal text run to another bbox at a
                # wrapping threshold.  That changes the applicable aim line
                # discontinuously, so endpoint-only binary search cannot see
                # every candidate (nor assume a signed aim delta is monotone).
                # Search the already-bounded feasible interval at the public
                # 0.05pt precision and score each real Qt plan by its actual
                # terminal bbox.  This is intentionally limited to the rare
                # multi-bbox paragraph path; normal one-bbox fitting retains
                # its inexpensive aim-crossing binary search below.
                candidates = self._multi_bbox_aim_candidates(
                    text, replacement, page_sizes, style, lower, feasible_lower,
                )
                return _closest_to_aim((*candidates, best))

            # The largest feasible size is not necessarily closest to a bbox
            # bottom: crossing the aim line can happen before the forbidden
            # line.  Search the terminal-line crossing and compare its two
            # sides instead of treating the feasibility boundary as the goal.
            if _terminal_aim_delta(minimum_plan) <= 0 <= _terminal_aim_delta(best):
                aim_lower, aim_upper = lower, feasible_lower
                before, after = minimum_plan, best
                for _ in range(_MAX_FONT_SIZE_SEARCH_ITERATIONS):
                    if aim_upper - aim_lower <= _FONT_SIZE_TOLERANCE:
                        break
                    middle = (aim_lower + aim_upper) / 2
                    fitted = self._plan(text, replacement, page_sizes, style, middle)
                    if fitted is None:
                        aim_upper = middle
                        continue
                    if _terminal_aim_delta(fitted) <= 0:
                        before = fitted
                        aim_lower = middle
                    else:
                        after = fitted
                        aim_upper = middle
                return _closest_to_aim((before, after, best))
            return _closest_to_aim((minimum_plan, best))
        finally:
            self._layout_obstacles = previous_obstacles

    def _multi_bbox_aim_candidates(
        self,
        text: str,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        style: PatchTextStyle,
        lower: float,
        upper: float,
    ) -> tuple[FittedParagraph, ...]:
        """Sample all 0.05pt multi-bbox candidates inside a feasible interval.

        Text flow can jump from one source rectangle to the next when Qt
        changes a wrap decision.  The terminal aim line then changes too, so
        a binary search over only endpoint deltas is unsound.  ``upper`` is
        already the largest feasible side of the normal bracketing search;
        every returned plan remains subject to its forbidden line.
        """
        step_count = int((upper - lower) / _FONT_SIZE_TOLERANCE)
        sizes = [lower + step * _FONT_SIZE_TOLERANCE for step in range(step_count + 1)]
        if not sizes or upper - sizes[-1] > 1e-9:
            sizes.append(upper)
        candidates = tuple(
            plan
            for size in sizes
            if (plan := self._plan(text, replacement, page_sizes, style, size)) is not None
            and plan.placements
        )
        # ``lower`` was already proven feasible; retain a defensive fallback
        # for unusual floating-point intervals.
        if candidates:
            return candidates
        fallback = self._plan(text, replacement, page_sizes, style, lower)
        assert fallback is not None
        return (fallback,)

    def _find_automatic_font_size_upper_bound(
        self,
        text: str,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        style: PatchTextStyle,
        lower: float,
    ) -> float:
        """Grow an automatic font-size interval until its upper side fails.

        The numeric probe is only a starting point.  Every finite source
        rectangle eventually rejects a sufficiently large QTextLayout line,
        so this avoids treating a typographic default as a visual ceiling.
        """
        upper = max(_AUTOMATIC_FONT_SIZE_INITIAL, lower)
        for _ in range(_MAX_AUTOMATIC_FONT_SIZE_BRACKETING_ITERATIONS):
            if self._plan(text, replacement, page_sizes, style, upper) is None:
                return upper
            upper *= 2
        raise ValueError("automatic font-size search could not bracket a failing upper bound")

    def fit_headline(
        self,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        minimum_font_size: float,
    ) -> FittedParagraph:
        """Fit a headline normally, or draw it as one natural-width line.

        Headline font minima are a normal part of the document's typography,
        not a reason to abort an otherwise usable translation.  A narrow
        source rectangle first receives the ordinary multi-region treatment.
        If that cannot hold the headline at its required size, the first
        rectangle supplies only the left/vertical anchor and the unwrapped
        line is allowed to continue to the right.
        """
        try:
            return self.fit(replacement, page_sizes, minimum_font_size)
        except ValueError as fit_error:
            previous_obstacles = self._layout_obstacles
            if not self._uses_window_obstacles:
                self._layout_obstacles = _replacement_obstacles(replacement, page_sizes)
            try:
                return self._plan_headline_overflow(
                    replacement, page_sizes, minimum_font_size,
                )
            except ValueError as overflow_error:
                raise overflow_error from fit_error
            finally:
                self._layout_obstacles = previous_obstacles

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
                self.prepare_automatic_font(_contains_cjk(text))
                resolution = self._auto_font_resolution
                assert resolution is not None
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
        capacities = tuple(
            region_in_page_points(region, *page_sizes[region.page_index])
            for region in replacement.source_regions()
        )
        text, spans = self._formula_layout_text(
            text, replacement, style, font_size,
            max(rectangle.width - 2 * style.horizontal_padding for rectangle in capacities),
            max(rectangle.height - 2 * style.vertical_padding for rectangle in capacities),
        )
        if len(replacement.source_regions()) == 1:
            source_region = replacement.source_regions()[0]
            page_width, page_height = page_sizes[source_region.page_index]
            rectangle = region_in_page_points(source_region, page_width, page_height)
            forbidden_bottom = _forbidden_bottom(
                rectangle,
                self._layout_obstacles.get(source_region.page_index, ()),
                page_height,
            )
            previous_forbidden_bottom = self._active_forbidden_bottom
            self._active_forbidden_bottom = forbidden_bottom
            try:
                placement, consumed = self._fit_region(
                    source_region.page_index, rectangle, text, style, font_size, spans,
                )
            finally:
                self._active_forbidden_bottom = previous_forbidden_bottom
            if placement is None or consumed != len(text):
                return None
            return FittedParagraph(text, font_size, (placement,))
        return self._plan_continuous_flow(text, spans, replacement, page_sizes, style, font_size)

    def _plan_continuous_flow(
        self,
        text: str,
        formula_spans: tuple[_FormulaSpan, ...],
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        style: PatchTextStyle,
        font_size: float,
    ) -> FittedParagraph | None:
        """Try compact row-capacity plans against one continuous QTextLayout.

        The planning phase holds only one decision per source region: a count
        of nominal rows plus its signed distance to the source bbox aim.  It
        never expands that count into a list of virtual rows.  Only the chosen
        plan creates real QTextLine objects, one at a time, so malformed tiny
        font/line-height inputs cannot allocate an astronomical row array.
        """
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)

        @dataclass
        class _FlowRegion:
            decision: _RegionSlotDecision
            available_width: float
            available_top: float
            content_left: float
            y: float
            line_text_lefts: list[float] = field(default_factory=list)
            line_text_widths: list[float] = field(default_factory=list)
            line_tops: list[float] = field(default_factory=list)
            line_heights: list[float] = field(default_factory=list)
            formula_draws: list[FormulaDraw] = field(default_factory=list)
            start_utf16: int | None = None
            end_utf16: int | None = None
            last_line_height: float = 0.0

        decision_plans = self._slot_decision_plans(
            QtCore, QtGui, replacement, page_sizes, style, font_size,
        )
        if not decision_plans:
            return None

        utf16_formula_spans = tuple(
            _FormulaSpan(
                _utf16_index_for_python(text, span.start),
                _utf16_index_for_python(text, span.start + span.length)
                - _utf16_index_for_python(text, span.start),
                span.fragment,
                span.latex,
            )
            for span in formula_spans
        )
        attempts = tuple(
            paragraph
            for plan in decision_plans
            if (paragraph := self._layout_slot_plan(
                QtCore, QtGui, text, utf16_formula_spans, plan, style, font_size, _FlowRegion,
            )) is not None
        )
        return _closest_to_aim(attempts) if attempts else None

    def _slot_decision_plans(
        self,
        QtCore,
        QtGui,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        style: PatchTextStyle,
        font_size: float,
    ) -> tuple[_SlotDecisionPlan, ...]:
        """Build at most two compact, signed-aim plans without row objects."""
        metric_layout = self._create_layout(QtCore, QtGui, "", style, font_size, _LAYOUT_SCALE)
        nominal_height = float(QtGui.QFontMetricsF(metric_layout.font()).height())
        nominal_advance = nominal_height * style.line_height
        if not _positive_finite(nominal_height) or not _positive_finite(nominal_advance):
            return ()

        choices: list[tuple[_RegionSlotDecision, ...]] = []
        for source_region in replacement.source_regions():
            page_width, page_height = page_sizes[source_region.page_index]
            rectangle = region_in_page_points(source_region, page_width, page_height)
            scaled = _scale_rectangle(rectangle)
            content_top = scaled.top + style.vertical_padding * _LAYOUT_SCALE
            aim = scaled.bottom - style.vertical_padding * _LAYOUT_SCALE
            width = scaled.width - 2 * style.horizontal_padding * _LAYOUT_SCALE
            if width <= 0 or aim <= content_top:
                return ()
            top_boundary, bottom_boundary = _vertical_boundaries(
                rectangle,
                self._layout_obstacles.get(source_region.page_index, ()),
                page_height,
            )
            loose_count = max(0, int((aim - content_top - nominal_height) // nominal_advance) + 1)
            loose_bottom = (
                content_top + nominal_height + (loose_count - 1) * nominal_advance
                if loose_count else content_top
            )
            loose_delta = aim - loose_bottom
            loose = _RegionSlotDecision(
                source_region.page_index, rectangle, loose_count, loose_delta,
                loose_delta / 2, top_boundary * _LAYOUT_SCALE,
                bottom_boundary * _LAYOUT_SCALE, "loose",
            )
            tight_count = loose_count + 1
            tight_bottom = content_top + nominal_height + (tight_count - 1) * nominal_advance
            tight_delta = aim - tight_bottom
            assert tight_delta < 1e-6
            clearance = min(
                scaled.top - top_boundary * _LAYOUT_SCALE,
                bottom_boundary * _LAYOUT_SCALE - scaled.bottom,
            )
            candidates = [loose]
            if clearance >= 0 and -tight_delta / 2 <= clearance + 1e-6:
                candidates.append(_RegionSlotDecision(
                    source_region.page_index, rectangle, tight_count, tight_delta,
                    tight_delta / 2, top_boundary * _LAYOUT_SCALE,
                    bottom_boundary * _LAYOUT_SCALE, "tight",
                ))
            choices.append(tuple(candidates))
        return _signed_slot_plan_frontier(tuple(choices))

    def _layout_slot_plan(
        self,
        QtCore,
        QtGui,
        text: str,
        formula_spans: tuple[_FormulaSpan, ...],
        plan: _SlotDecisionPlan,
        style: PatchTextStyle,
        font_size: float,
        flow_region_type,
    ) -> FittedParagraph | None:
        """Create actual QTextLines lazily for a selected compact plan."""
        regions = []
        for decision in plan.decisions:
            scaled = _scale_rectangle(decision.rectangle)
            top = scaled.top + style.vertical_padding * _LAYOUT_SCALE
            regions.append(flow_region_type(
                decision,
                scaled.width - 2 * style.horizontal_padding * _LAYOUT_SCALE,
                top,
                scaled.x + style.horizontal_padding * _LAYOUT_SCALE,
                top,
            ))
        layout = self._create_layout(QtCore, QtGui, text, style, font_size, _LAYOUT_SCALE)
        exhausted = False
        unused_slots = 0
        layout.beginLayout()
        try:
            # QTextLine owns the next unconsumed part of the continuous stream.
            # Keep one pending line while looking for a slot wide enough for an
            # inline formula.  Changing its width reflows that *same* line, so
            # a formula never gets split merely because the preceding slot is
            # too narrow.  Crucially the rows themselves remain virtual: this
            # loop creates a line only when there is still text to lay out.
            region_index = 0
            slot_index = 0
            line = None
            while region_index < len(regions):
                region = regions[region_index]
                if slot_index >= region.decision.line_count:
                    region_index += 1
                    slot_index = 0
                    continue
                if line is None:
                    line = layout.createLine()
                    if not line.isValid():
                        exhausted = True
                        unused_slots = (
                            region.decision.line_count - slot_index
                            + sum(item.decision.line_count for item in regions[region_index + 1:])
                        )
                        break
                line.setLineWidth(region.available_width)
                start = line.textStart()
                end = start + line.textLength()
                fragments = tuple(
                    span.fragment for span in formula_spans
                    if start <= span.start and span.start + span.length <= end
                )
                formula_does_not_fit = (
                    any(span.start < end < span.start + span.length for span in formula_spans)
                    or any(
                        _x_coordinate(line.cursorToX(span.start)) + span.fragment.width
                        > region.available_width + 1e-6
                        for span in formula_spans
                        if start <= span.start and span.start + span.length <= end
                    )
                )
                if formula_does_not_fit:
                    # Preserve the pending QTextLine and retry it in the next
                    # region.  If there is none, this candidate is infeasible;
                    # another font size or slot plan may still work.
                    region_index += 1
                    slot_index = 0
                    continue
                line_height = max((line.height(), *(fragment.height for fragment in fragments)))
                baseline = region.y + max((
                    line.ascent(), *(fragment.height - fragment.descent for fragment in fragments),
                ))
                line_top = baseline - line.ascent()
                line_start = _x_coordinate(line.cursorToX(start))
                line_end = _x_coordinate(line.cursorToX(end))
                actual_width = line_end + sum(
                    span.fragment.width - (
                        _x_coordinate(line.cursorToX(span.start + span.length))
                        - _x_coordinate(line.cursorToX(span.start))
                    )
                    for span in formula_spans if start <= span.start and span.start + span.length <= end
                )
                if actual_width > region.available_width + 1e-6:
                    return None
                region.line_text_lefts.append(region.content_left + line_start)
                region.line_text_widths.append(line_end - line_start)
                region.line_tops.append(line_top)
                region.line_heights.append(line_height)
                region.start_utf16 = start if region.start_utf16 is None else region.start_utf16
                region.end_utf16 = end
                region.last_line_height = line_height
                for span in formula_spans:
                    if start <= span.start and span.start + span.length <= end:
                        region.formula_draws.append(FormulaDraw(
                            span.fragment.pdf,
                            region.content_left + _x_coordinate(line.cursorToX(span.start)),
                            baseline, span.fragment.descent, span.latex,
                            _python_index_for_utf16(text, span.start)
                            - _python_index_for_utf16(text, region.start_utf16),
                            _python_index_for_utf16(text, span.start + span.length)
                            - _python_index_for_utf16(text, span.start),
                        ))
                region.y += line_height * style.line_height
                slot_index += 1
                line = None
            if not exhausted and layout.createLine().isValid():
                return None
        finally:
            layout.endLayout()

        placements: list[RegionTextPlacement] = []
        for region in regions:
            if region.start_utf16 is None or region.end_utf16 is None:
                continue
            actual_bottom = region.line_tops[-1] + region.line_heights[-1]
            aim = _scale_rectangle(region.decision.rectangle).bottom - style.vertical_padding * _LAYOUT_SCALE
            shift = (aim - actual_bottom) / 2
            shifted_top = region.line_tops[0] + shift
            shifted_bottom = actual_bottom + shift
            upper_guard = region.decision.top_boundary + style.vertical_padding * _LAYOUT_SCALE
            lower_guard = region.decision.bottom_boundary - style.vertical_padding * _LAYOUT_SCALE
            if shifted_top < upper_guard - 1e-6 or shifted_bottom > lower_guard + 1e-6:
                return None
            start = _python_index_for_utf16(text, region.start_utf16)
            end = _python_index_for_utf16(text, region.end_utf16)
            placements.append(RegionTextPlacement(
                region.decision.page_index, region.decision.rectangle, text[start:],
                tuple(value / _LAYOUT_SCALE for value in region.line_text_lefts),
                tuple(value / _LAYOUT_SCALE for value in region.line_text_widths),
                tuple((value + shift) / _LAYOUT_SCALE for value in region.line_tops),
                tuple(value / _LAYOUT_SCALE for value in region.line_heights),
                font_size, style,
                formula_draws=tuple(
                    FormulaDraw(
                        draw.pdf, draw.x / _LAYOUT_SCALE,
                        (draw.baseline + shift) / _LAYOUT_SCALE, draw.descent / _LAYOUT_SCALE,
                        draw.latex, draw.proxy_start, draw.proxy_length,
                    ) for draw in region.formula_draws
                ),
                assigned_text=text[start:end],
                forbidden_bottom=region.decision.bottom_boundary / _LAYOUT_SCALE,
            ))
        return FittedParagraph(
            text, font_size, tuple(placements),
            len(plan.decisions) - len(placements), unused_slots,
        )

    def _plan_headline_overflow(
        self,
        replacement: PDFReplacement,
        page_sizes: dict[int, tuple[float, float]],
        minimum_font_size: float,
    ) -> FittedParagraph:
        """Return an unwrapped headline anchored at its first source box."""
        text = self._materialize_formula_fallbacks(replacement)
        if not text:
            raise ValueError("replacement text must not be empty")
        style = self.options.style_for(replacement.layout_ref, replacement.layout_level)
        # A body-relative headline minimum is preferred, but a numeric maximum
        # is an explicit user ceiling for every semantic level. Width is the
        # only relaxed bound in the overflow layout.
        effective_minimum = max(style.min_font_size, minimum_font_size)
        if style.max_font_size is not None:
            effective_minimum = min(effective_minimum, style.max_font_size)
        style = self._resolve_font(style, text)
        self._validate_style(style)

        region = replacement.source_regions()[0]
        page_width, page_height = page_sizes[region.page_index]
        rectangle = region_in_page_points(region, page_width, page_height)
        forbidden_bottom = _forbidden_bottom(
            rectangle,
            self._layout_obstacles.get(region.page_index, ()),
            page_height,
        )
        # The overflow rule is intentionally left aligned to the physical box
        # edge and vertically centered on that edge, independent of the
        # ordinary paragraph's padding/alignment settings.
        overflow_style = replace(
            style,
            horizontal_padding=0.0,
            vertical_padding=0.0,
            alignment="left",
        )
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        layout = self._create_layout(
            QtCore, QtGui, text, overflow_style, effective_minimum, _LAYOUT_SCALE,
        )
        layout.beginLayout()
        try:
            line = layout.createLine()
            if not line.isValid():
                raise ValueError("Qt could not create a headline line")
            line.setLineWidth(_HEADLINE_OVERFLOW_LINE_WIDTH * _LAYOUT_SCALE)
            line_height = line.height()
            line_start = _x_coordinate(line.cursorToX(0))
            line_end = _x_coordinate(line.cursorToX(line.textLength()))
        finally:
            layout.endLayout()
        if line.textLength() <= 0:
            raise ValueError("Qt could not lay out headline text")
        line_top = rectangle.top + (rectangle.height - line_height / _LAYOUT_SCALE) / 2
        if line_top + line_height / _LAYOUT_SCALE > forbidden_bottom + 1e-6:
            raise ValueError(
                "headline overflow cannot fit before the lower obstacle or page boundary"
            )
        placement = RegionTextPlacement(
            region.page_index,
            rectangle,
            text,
            (rectangle.x + line_start / _LAYOUT_SCALE,),
            ((line_end - line_start) / _LAYOUT_SCALE,),
            (line_top,),
            (line_height / _LAYOUT_SCALE,),
            effective_minimum,
            overflow_style,
            allows_horizontal_overflow=True,
            assigned_text=text,
            forbidden_bottom=forbidden_bottom,
        )
        return FittedParagraph(text, effective_minimum, (placement,))

    def _fit_region(
        self,
        page_index: int,
        rectangle: PageRectangle,
        text: str,
        style: PatchTextStyle,
        font_size: float,
        formula_spans: tuple[_FormulaSpan, ...] = (),
        ignore_height: bool = False,
    ) -> tuple[RegionTextPlacement | None, int]:
        scaled_rectangle = _scale_rectangle(rectangle)
        available_width = scaled_rectangle.width - 2 * style.horizontal_padding * _LAYOUT_SCALE
        available_top = scaled_rectangle.top + style.vertical_padding * _LAYOUT_SCALE
        forbidden_bottom = self._active_forbidden_bottom
        hard_bottom = forbidden_bottom if forbidden_bottom is not None else rectangle.bottom
        available_bottom = hard_bottom * _LAYOUT_SCALE - style.vertical_padding * _LAYOUT_SCALE
        if available_width <= 0 or available_bottom <= available_top:
            return None, 0

        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        layout = self._create_layout(QtCore, QtGui, text, style, font_size, _LAYOUT_SCALE)
        # Formula spans retain Python indexes so _plan can slice the remaining
        # text safely.  QTextLine positions, however, are absolute UTF-16
        # units, so map the spans once for every current remaining-text layout.
        # This matters for astral characters before a formula.
        utf16_formula_spans = tuple(
            _FormulaSpan(
                _utf16_index_for_python(text, span.start),
                _utf16_index_for_python(text, span.start + span.length)
                - _utf16_index_for_python(text, span.start),
                span.fragment,
                span.latex,
            )
            for span in formula_spans
        )
        content_left = scaled_rectangle.x + style.horizontal_padding * _LAYOUT_SCALE
        line_tops: list[float] = []
        line_text_lefts: list[float] = []
        line_text_widths: list[float] = []
        line_heights: list[float] = []
        formula_draws: list[FormulaDraw] = []
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
                line_start_index = line.textStart()
                line_end_index = line_start_index + line.textLength()
                line_fragments = tuple(
                    span.fragment for span in utf16_formula_spans
                    if line_start_index <= span.start and span.start + span.length <= line_end_index
                )
                if line_fragments:
                    line_height = max(line_height, *(fragment.height for fragment in line_fragments))
                # A frozen one-line OCR region may ignore its tight source
                # bbox, but never the actual lower obstacle/page boundary.
                # ``forbidden_bottom is None`` is the legacy direct-call
                # spelling for “no independently computed forbidden line”.
                if (
                    (not ignore_height or forbidden_bottom is not None)
                    and y + line_height > available_bottom + 1e-6
                ):
                    break
                # QTextLayout may wrap anywhere.  Never accept a line that
                # cuts through one formula's width proxy: leave the complete
                # atom for the next source rectangle instead.
                if any(
                    span.start < line_end_index < span.start + span.length
                    for span in utf16_formula_spans
                ):
                    break
                if any(
                    _x_coordinate(line.cursorToX(span.start))
                    + span.fragment.width > available_width + 1e-6
                    for span in utf16_formula_spans
                    if line_start_index <= span.start and span.start + span.length <= line_end_index
                ):
                    break
                line_formula_spans = tuple(
                    span for span in utf16_formula_spans
                    if line_start_index <= span.start and span.start + span.length <= line_end_index
                )
                actual_line_width = _x_coordinate(line.cursorToX(line_end_index)) + sum(
                    span.fragment.width - (
                        _x_coordinate(line.cursorToX(span.start + span.length))
                        - _x_coordinate(line.cursorToX(span.start))
                    )
                    for span in line_formula_spans
                )
                if actual_line_width > available_width + 1e-6:
                    break
                line_baseline = y + max((
                    line.ascent(),
                    *(fragment.height - fragment.descent for fragment in line_fragments),
                ))
                line_tops.append(line_baseline - line.ascent())
                line_start = _x_coordinate(line.cursorToX(line_start_index))
                line_end = _x_coordinate(line.cursorToX(line_end_index))
                line_text_lefts.append(content_left + line_start)
                line_text_widths.append(line_end - line_start)
                line_heights.append(line_height)
                for span in line_formula_spans:
                    if line_start_index <= span.start and span.start + span.length <= line_end_index:
                        formula_draws.append(FormulaDraw(
                            span.fragment.pdf,
                            content_left + _x_coordinate(line.cursorToX(span.start)),
                            line_baseline,
                            span.fragment.descent,
                            span.latex,
                            _python_index_for_utf16(text, span.start),
                            _python_index_for_utf16(text, span.start + span.length)
                            - _python_index_for_utf16(text, span.start),
                        ))
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
        consumed = _python_index_for_utf16(text, consumed_utf16)
        return RegionTextPlacement(
            page_index,
            rectangle,
            text,
            tuple(value / _LAYOUT_SCALE for value in line_text_lefts),
            tuple(value / _LAYOUT_SCALE for value in line_text_widths),
            tuple((top + shift) / _LAYOUT_SCALE for top in line_tops),
            tuple(value / _LAYOUT_SCALE for value in line_heights),
            font_size,
            style,
            formula_draws=tuple(
                FormulaDraw(
                    draw.pdf, draw.x / _LAYOUT_SCALE,
                    (draw.baseline + shift) / _LAYOUT_SCALE,
                    draw.descent / _LAYOUT_SCALE,
                    draw.latex, draw.proxy_start, draw.proxy_length,
                )
                for draw in formula_draws
            ),
            assigned_text=text[:consumed],
            forbidden_bottom=forbidden_bottom,
        ), consumed

    def _draw_placement(self, QtCore, QtGui, painter, placement: RegionTextPlacement) -> None:
        layout = self._create_layout(
            QtCore, QtGui, placement.assigned_text or placement.remaining_text,
            placement.style, placement.font_size, _LAYOUT_SCALE,
        )
        if placement.allows_horizontal_overflow:
            available_width = _HEADLINE_OVERFLOW_LINE_WIDTH * _LAYOUT_SCALE
            x = placement.rectangle.x * _LAYOUT_SCALE
        else:
            available_width = (
                placement.rectangle.width - 2 * placement.style.horizontal_padding
            ) * _LAYOUT_SCALE
            x = (placement.rectangle.x + placement.style.horizontal_padding) * _LAYOUT_SCALE
        layout.beginLayout()
        try:
            for top in placement.line_tops:
                line = layout.createLine()
                if not line.isValid():
                    raise RuntimeError("Qt layout changed while drawing a planned paragraph")
                line.setLineWidth(available_width)
                line.setPosition(QtCore.QPointF(x, top * _LAYOUT_SCALE))
        finally:
            layout.endLayout()
        painter.save()
        painter.scale(1 / _LAYOUT_SCALE, 1 / _LAYOUT_SCALE)
        try:
            layout.draw(painter, QtCore.QPointF(0, 0))
        finally:
            painter.restore()

    def fit_frozen_region(
        self,
        placement: RegionTextPlacement,
        target_font_size: float,
        minimum_font_size: float | None = None,
    ) -> RegionTextPlacement:
        """Project one already-assigned region towards a local target size.

        The first paragraph pass owns text flow between source rectangles.  A
        second pass may only reflow the text it consumed itself, and only while
        preserving its exact line count.  One-line OCR boxes intentionally do
        not constrain vertical glyph bounds: their height is often just the
        tight ink box rather than a typographic line box.  The independently
        computed forbidden line (a lower obstacle or page bottom) remains a
        hard constraint in every case.
        """
        text = placement.assigned_text or placement.remaining_text
        if not text:
            return placement
        expected_lines = len(placement.line_tops)
        if not expected_lines:
            return placement

        style = placement.style
        original = placement.font_size
        minimum = max(style.min_font_size, minimum_font_size or style.min_font_size)
        target = max(target_font_size, minimum)
        if style.max_font_size is not None:
            target = min(target, style.max_font_size)
        if placement.allows_horizontal_overflow:
            return self._fit_frozen_overflow_region(placement, text, target)

        def candidate(size: float) -> RegionTextPlacement | None:
            local_text, formula_spans = self._formula_spans_for_frozen_region(
                placement, size,
            )
            if local_text is None:
                return None
            previous_forbidden_bottom = self._active_forbidden_bottom
            self._active_forbidden_bottom = placement.forbidden_bottom
            try:
                fitted, consumed = self._fit_region(
                    placement.page_index, placement.rectangle, local_text, style, size, formula_spans,
                    ignore_height=expected_lines == 1,
                )
            finally:
                self._active_forbidden_bottom = previous_forbidden_bottom
            if (
                fitted is None
                or consumed != len(local_text)
                or len(fitted.line_tops) != expected_lines
            ):
                return None
            return fitted

        exact = candidate(target)
        if exact is not None:
            return exact
        # Font-size changes are monotone for a fixed local string in normal Qt
        # wrapping.  Keep the first-pass size as the known feasible endpoint
        # and approach the page-level target without crossing a line-count or
        # bbox boundary.  The 0.05pt tolerance is shared with first-pass fit.
        low, high = sorted((original, target))
        best = candidate(original) or placement
        for _ in range(_MAX_FONT_SIZE_SEARCH_ITERATIONS):
            if high - low <= _FONT_SIZE_TOLERANCE:
                break
            middle = (low + high) / 2
            fitted = candidate(middle)
            if target > original:
                if fitted is None:
                    high = middle
                else:
                    best = fitted
                    low = middle
            else:
                if fitted is None:
                    low = middle
                else:
                    best = fitted
                    high = middle
        return best

    def _fit_frozen_overflow_region(
        self,
        placement: RegionTextPlacement,
        text: str,
        font_size: float,
    ) -> RegionTextPlacement:
        """Re-layout one headline as its required natural-width overflow line.

        Overflow is a headline-specific constraint relaxation: it keeps the
        physical bbox's left edge and vertical centre, but never wraps or
        clips at its right edge.  That remains true during local
        normalization, so a same-level page target can update the font size
        without turning an intentionally overflowing headline into a normal
        constrained paragraph.
        """
        if placement.formula_draws:
            # Overflow headlines currently materialize inline formulas as
            # text before this path.  Retaining an unexpected vector atom is
            # safer than drawing it with coordinates measured at another size.
            return placement
        QtCore, QtGui = _qt_modules()
        _ensure_qt_application(QtGui)
        layout = self._create_layout(
            QtCore, QtGui, text, placement.style, font_size, _LAYOUT_SCALE,
        )
        layout.beginLayout()
        try:
            line = layout.createLine()
            if not line.isValid():
                return placement
            line.setLineWidth(_HEADLINE_OVERFLOW_LINE_WIDTH * _LAYOUT_SCALE)
            if line.textLength() != _utf16_index_for_python(text, len(text)):
                return placement
            line_height = line.height()
            line_start = _x_coordinate(line.cursorToX(0))
            line_end = _x_coordinate(line.cursorToX(line.textLength()))
        finally:
            layout.endLayout()
        rectangle = placement.rectangle
        line_top = rectangle.top + (rectangle.height - line_height / _LAYOUT_SCALE) / 2
        if (
            placement.forbidden_bottom is not None
            and line_top + line_height / _LAYOUT_SCALE > placement.forbidden_bottom + 1e-6
        ):
            if (
                placement.line_tops[-1] + placement.line_heights[-1]
                > placement.forbidden_bottom + 1e-6
            ):
                raise ValueError("frozen headline overflow already exceeds its forbidden line")
            return placement
        return RegionTextPlacement(
            placement.page_index,
            rectangle,
            text,
            (rectangle.x + line_start / _LAYOUT_SCALE,),
            ((line_end - line_start) / _LAYOUT_SCALE,),
            (line_top,),
            (line_height / _LAYOUT_SCALE,),
            font_size,
            placement.style,
            allows_horizontal_overflow=True,
            assigned_text=text,
            forbidden_bottom=placement.forbidden_bottom,
        )

    def _formula_spans_for_frozen_region(
        self, placement: RegionTextPlacement, font_size: float,
    ) -> tuple[str | None, tuple[_FormulaSpan, ...]]:
        """Rebuild vector formula proxies for one already-owned text run.

        Formula fragments depend on point size.  Keeping their old PDFs while
        changing nearby text size would make Qt reserve one width and the
        composer draw another.  We therefore re-render every atom from the
        source retained in ``FormulaDraw``.  If that cannot be done, retaining
        the first-pass placement is safer than degrading the formula or
        splitting it across a region boundary.
        """
        text = placement.assigned_text or placement.remaining_text
        if not placement.formula_draws:
            return text, ()
        if not self.options.render_inline_formulas or not self._formula_renderer.available:
            return None, ()
        draws = tuple(sorted(placement.formula_draws, key=lambda draw: draw.proxy_start))
        if any(not draw.latex or draw.proxy_length <= 0 for draw in draws):
            return None, ()

        QtCore, QtGui = _qt_modules()
        del QtCore
        _ensure_qt_application(QtGui)
        font = QtGui.QFont(placement.style.font_name or "")
        font.setPointSizeF(font_size * _LAYOUT_SCALE)
        space_width = float(QtGui.QFontMetricsF(font).horizontalAdvance("\u00a0"))
        if space_width <= 0:
            return None, ()
        maximum_width = (
            placement.rectangle.width - 2 * placement.style.horizontal_padding
        ) * _LAYOUT_SCALE
        maximum_height = (
            placement.rectangle.height - 2 * placement.style.vertical_padding
        ) * _LAYOUT_SCALE
        parts: list[str] = []
        spans: list[_FormulaSpan] = []
        cursor = 0
        offset = 0
        for draw in draws:
            start = draw.proxy_start
            end = start + draw.proxy_length
            if start < cursor or end > len(text) or text[start:end] != "\u00a0" * draw.proxy_length:
                return None, ()
            raw_fragment = self._formula_renderer.render(draw.latex, font_size)
            fragment = _scale_formula_fragment(raw_fragment) if raw_fragment is not None else None
            if (
                fragment is None
                or fragment.width > maximum_width
                or (len(placement.line_tops) != 1 and fragment.height > maximum_height)
            ):
                return None, ()
            prefix = text[cursor:start]
            parts.append(prefix)
            offset += len(prefix)
            length = max(1, round(fragment.width / space_width))
            parts.append("\u00a0" * length)
            spans.append(_FormulaSpan(offset, length, fragment, draw.latex))
            offset += length
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts), tuple(spans)

    @staticmethod
    def _create_layout(
        QtCore, QtGui, text: str, style: PatchTextStyle, font_size: float, scale: float = 1.0,
    ):
        families = tuple(
            family for family in (style.font_name, *style.fallback_fonts) if family
        )
        font = QtGui.QFont(families[0]) if families else QtGui.QFont()
        if len(families) > 1:
            font.setFamilies(list(families))
        font.setPointSizeF(font_size * scale)
        font.setWeight(QtGui.QFont.Weight(style.font_weight))
        option = QtGui.QTextOption()
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
        if style.min_font_size <= 0 or (
            style.max_font_size is not None and style.max_font_size < style.min_font_size
        ):
            raise ValueError("font sizes must be positive and max_font_size >= min_font_size")
        if style.line_height <= 0:
            raise ValueError("line_height must be positive")
        if style.horizontal_padding < 0 or style.vertical_padding < 0:
            raise ValueError("text padding must be non-negative")
        if style.alignment not in {"left", "center", "right", "justify"}:
            raise ValueError(f"unsupported text alignment: {style.alignment}")
        if style.vertical_alignment not in {"top", "center", "bottom"}:
            raise ValueError(f"unsupported vertical alignment: {style.vertical_alignment}")

    def _materialize_formula_fallbacks(self, replacement: PDFReplacement) -> str:
        """Replace every structural formula marker with readable Unicode text.

        A formula PDF fragment is only useful once it can be inserted as a Qt
        inline object with its own baseline metrics.  QTextLayout has no
        public object-handler API, so the text layer deliberately chooses the
        safe, selectable Unicode representation until that object bridge is
        available.  The local renderer remains independently available for
        the PDF composer and, critically, failure never exposes raw LaTeX.
        """
        text = replacement.text
        markers = text.count("\ufffc")
        if markers != len(replacement.inline_formulas):
            raise ValueError("PDF replacement formula markers do not match inline formulas")
        if not markers:
            return text
        # Calling render here probes and caches availability per point size in
        # the normal fitting loop.  Even with a working TeX installation the
        # fallback remains intentional until a baseline-aware Qt object run is
        # introduced; this avoids emitting a mis-positioned formula fragment.
        return _replace_formula_markers(text, replacement.inline_formulas)

    def _formula_layout_text(
        self, text: str, replacement: PDFReplacement, style: PatchTextStyle, font_size: float,
        maximum_width: float, maximum_height: float,
    ) -> tuple[str, tuple[_FormulaSpan, ...]]:
        """Build invisible, non-breaking width proxies for usable formula PDFs.

        The proxy is an atom-sized NBSP run: Qt includes it in ordinary line
        breaking and alignment, while the composer later replaces its visual
        area with the transparent vector fragment at the measured baseline.
        A literal space immediately after a rendered formula is made
        non-breaking in this private layout representation.  It retains the
        original space's visual advance, but belongs to the formula atom so
        Qt cannot leave the formula at a source-region edge and start the
        next line with the following text.  This is deliberately formula
        structure, not a language- or punctuation-specific line-break rule.
        A fragment that cannot be rendered is immediately substituted with
        Unicode text instead, so one bad TeX expression never fails a page.
        """
        maximum_width *= _LAYOUT_SCALE
        maximum_height *= _LAYOUT_SCALE
        if not replacement.inline_formulas:
            return text, ()
        if text.count("\ufffc") != len(replacement.inline_formulas):
            raise ValueError("PDF replacement formula markers do not match inline formulas")
        if not self.options.render_inline_formulas or not self._formula_renderer.available:
            return _replace_formula_markers(text, replacement.inline_formulas), ()
        QtCore, QtGui = _qt_modules()
        del QtCore
        _ensure_qt_application(QtGui)
        font = QtGui.QFont(style.font_name or "")
        font.setPointSizeF(font_size * _LAYOUT_SCALE)
        space_width = float(QtGui.QFontMetricsF(font).horizontalAdvance("\u00a0"))
        parts: list[str] = []
        spans: list[_FormulaSpan] = []
        formula_index = 0
        offset = 0
        index = 0
        while index < len(text):
            character = text[index]
            if character != "\ufffc":
                parts.append(character)
                offset += 1
                index += 1
                continue
            formula = replacement.inline_formulas[formula_index]
            formula_index += 1
            raw_fragment = self._formula_renderer.render(formula.latex, font_size)
            fragment = _scale_formula_fragment(raw_fragment) if raw_fragment is not None else None
            if fragment is None or fragment.width > maximum_width or fragment.height > maximum_height:
                fallback = latex_to_plain_text(formula.latex)
                parts.append(fallback)
                offset += len(fallback)
                index += 1
                continue
            length = max(1, round(fragment.width / space_width))
            parts.append("\u00a0" * length)
            spans.append(_FormulaSpan(offset, length, fragment, formula.latex))
            offset += length
            index += 1
            # The replacement's logical text remains untouched.  Only the
            # formula's private Qt proxy absorbs its immediate ASCII spacer,
            # preserving the width while making the inline atom indivisible.
            if index < len(text) and text[index] == " ":
                parts.append("\u00a0")
                offset += 1
                index += 1
        return "".join(parts), tuple(spans)


def _replace_formula_markers(text: str, formulas) -> str:
    """Substitute ordered object markers without accepting raw delimiters."""
    iterator = iter(formulas)
    return "".join(
        latex_to_plain_text(next(iterator).latex) if character == "\ufffc" else character
        for character in text
    )


def _scale_rectangle(rectangle: PageRectangle) -> PageRectangle:
    """Map public PDF-point geometry into Qt's private high-DPI space."""
    return PageRectangle(
        rectangle.x * _LAYOUT_SCALE,
        rectangle.top * _LAYOUT_SCALE,
        rectangle.width * _LAYOUT_SCALE,
        rectangle.height * _LAYOUT_SCALE,
    )


def _scale_formula_fragment(fragment: FormulaFragment) -> FormulaFragment:
    """Use an ordinary point-sized formula PDF in scaled Qt measurements."""
    return FormulaFragment(
        fragment.pdf,
        fragment.width * _LAYOUT_SCALE,
        fragment.height * _LAYOUT_SCALE,
        fragment.descent * _LAYOUT_SCALE,
    )


def _forbidden_bottom(
    rectangle: PageRectangle,
    obstacles: Iterable[PageRectangle],
    page_height: float,
) -> float:
    """Return the first lower obstacle sharing horizontal space with ``rectangle``.

    The source bbox bottom remains an aim only.  An obstacle must start at or
    below that aim and overlap the bbox's horizontal projection; boxes above
    or merely touching an edge cannot restrict the downward flow.  The page
    edge is always the final forbidden line.
    """
    right = rectangle.x + rectangle.width
    candidates = [page_height]
    for obstacle in obstacles:
        obstacle_right = obstacle.x + obstacle.width
        if obstacle.top + 1e-6 < rectangle.bottom:
            continue
        if obstacle.x >= right - 1e-6 or obstacle_right <= rectangle.x + 1e-6:
            continue
        candidates.append(obstacle.top)
    return min(candidates)


def _vertical_boundaries(
    rectangle: PageRectangle,
    obstacles: Iterable[PageRectangle],
    page_height: float,
) -> tuple[float, float]:
    """Return nearest projected guards above and below one source bbox.

    A tight row-count plan may extend beyond both edges after its content is
    vertically centred.  The caller therefore needs the symmetric guards,
    rather than only the lower boundary used by legacy top-aligned fitting.
    Identical geometry is this region's own source entry and is not an
    obstacle; other rectangles remain protective, including a sibling source
    region that would visibly collide on the same page.
    """
    right = rectangle.x + rectangle.width
    above = [0.0]
    below = [page_height]
    for obstacle in obstacles:
        if obstacle == rectangle:
            continue
        obstacle_right = obstacle.x + obstacle.width
        if obstacle.x >= right - 1e-6 or obstacle_right <= rectangle.x + 1e-6:
            continue
        if obstacle.bottom <= rectangle.top + 1e-6:
            above.append(obstacle.bottom)
        elif obstacle.top >= rectangle.bottom - 1e-6:
            below.append(obstacle.top)
    return max(above), min(below)


def _positive_finite(value: float) -> bool:
    """Keep malformed geometry from creating unbounded virtual capacity."""
    return isfinite(value) and value > 0


def _signed_slot_plan_frontier(
    choices: tuple[tuple[_RegionSlotDecision, ...], ...],
) -> tuple[_SlotDecisionPlan, ...]:
    """Keep a bounded signed-distance frontier instead of enumerating 2**N.

    Each region supplies one loose and, when safe, one tight choice.  The
    frontier retains equally bounded candidates from both signs after every
    step, enough to return the nearest positive and negative total aim delta
    without retaining a virtual line for any candidate.
    """
    states = [_SlotDecisionPlan((), 0.0)]
    per_side = max(1, _SLOT_PLAN_BEAM_WIDTH // 2)
    for region_choices in choices:
        expanded = [
            _SlotDecisionPlan(state.decisions + (choice,), state.signed_delta + choice.delta_to_aim)
            for state in states for choice in region_choices
        ]
        non_negative = sorted(
            (state for state in expanded if state.signed_delta >= -1e-6),
            key=lambda state: (abs(state.signed_delta), -sum(
                decision.line_count for decision in state.decisions
            )),
        )[:per_side]
        negative = sorted(
            (state for state in expanded if state.signed_delta < -1e-6),
            key=lambda state: (abs(state.signed_delta), -sum(
                decision.line_count for decision in state.decisions
            )),
        )[:per_side]
        states = non_negative + negative
        if not states:
            return ()
    positive = min(
        (state for state in states if state.signed_delta >= -1e-6),
        default=None,
        key=lambda state: (abs(state.signed_delta), -sum(
            decision.line_count for decision in state.decisions
        )),
    )
    negative = min(
        (state for state in states if state.signed_delta < -1e-6),
        default=None,
        key=lambda state: (abs(state.signed_delta), -sum(
            decision.line_count for decision in state.decisions
        )),
    )
    if positive is None:
        return (negative,) if negative is not None else ()
    if negative is None or negative.decisions == positive.decisions:
        return (positive,)
    return positive, negative


def _replacement_obstacles(
    replacement: PDFReplacement,
    page_sizes: Mapping[int, tuple[float, float]],
) -> Mapping[int, tuple[PageRectangle, ...]]:
    """Build the direct-fit fallback obstacle map from known source geometry."""
    grouped: dict[int, list[PageRectangle]] = {}
    for region in (*replacement.source_regions(), *replacement.obstacle_regions):
        page_width, page_height = page_sizes[region.page_index]
        grouped.setdefault(region.page_index, []).append(
            region_in_page_points(region, page_width, page_height),
        )
    return {page_index: tuple(rectangles) for page_index, rectangles in grouped.items()}


def _window_obstacles(
    replacements: Iterable[PDFReplacement],
    page_sizes: Mapping[int, tuple[float, float]],
) -> Mapping[int, tuple[PageRectangle, ...]]:
    """Collect text, asset and formula geometry for one live layout window."""
    grouped: dict[int, list[PageRectangle]] = {}
    for replacement in replacements:
        for region in (*replacement.source_regions(), *replacement.obstacle_regions):
            page_width, page_height = page_sizes[region.page_index]
            grouped.setdefault(region.page_index, []).append(
                region_in_page_points(region, page_width, page_height),
            )
    return {page_index: tuple(rectangles) for page_index, rectangles in grouped.items()}


def _terminal_aim_delta(paragraph: FittedParagraph) -> float:
    """Signed terminal line distance from its source bbox aim line."""
    placement = paragraph.placements[-1]
    return placement.line_tops[-1] + placement.line_heights[-1] - placement.rectangle.bottom


def _aim_score(paragraph: FittedParagraph) -> tuple[int, int, float, float, float, int, float]:
    """Keep every occupied source region near its own target line.

    The final region is important, but cannot buy a perfect terminal fit by
    sending an earlier region all the way to its forbidden line.  Minimax
    scoring first limits the worst target-line deviation, then prefers the
    smaller aggregate deviation and finally a closer terminal line.
    """
    if not paragraph.placements:
        return (paragraph.unused_region_count, paragraph.unused_slot_count, float("inf"),
                float("inf"), float("inf"), 0, -paragraph.font_size)
    distances = tuple(
        abs(placement.line_tops[-1] + placement.line_heights[-1] - placement.rectangle.bottom)
        for placement in paragraph.placements
    )
    return (
        paragraph.unused_region_count,
        paragraph.unused_slot_count,
        max(distances), sum(distances), distances[-1],
        -len(paragraph.placements), -paragraph.font_size,
    )


def _closest_to_aim(paragraphs: Iterable[FittedParagraph]) -> FittedParagraph:
    return min(paragraphs, key=_aim_score)


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
        obstacles = _window_obstacles(replacements, self._page_sizes)
        set_layout_obstacles = getattr(self._filler, "set_layout_obstacles", None)
        if callable(set_layout_obstacles):
            set_layout_obstacles(obstacles)
        summaries: list[_PlannedParagraphSummary] = []
        body_font_sizes: dict[int, float] = {}
        storage = _WindowPlanStorage()

        try:
            body_replacements: dict[int, PDFReplacement] = {}
            body_statistics: dict[tuple[int, str, int], list[float]] = {}
            for replacement in body:
                paragraph = self._fit_or_skip(replacement, obstacles)
                if paragraph is None:
                    continue
                paragraph_index = len(summaries)
                summaries.append(_PlannedParagraphSummary(
                    replacement, paragraph.text, paragraph.font_size,
                ))
                for placement in paragraph.placements:
                    self._record_font_size_statistic(body_statistics, replacement, placement)
                storage.append(paragraph_index, replacement, paragraph)
                body_replacements[paragraph_index] = replacement
            body_targets = self._font_size_targets(body_statistics)
            self._normalize_stored_placements(storage, body_replacements, body_targets)
            body_font_sizes = self._stored_page_font_sizes(storage, body_replacements)

            headline_replacements: dict[int, PDFReplacement] = {}
            headline_minimums: dict[int, float] = {}
            headline_statistics: dict[tuple[int, str, int], list[float]] = {}
            for replacement in headlines:
                minimum = self._headline_minimum(replacement, body_font_sizes)
                paragraph = self._filler.fit_headline(replacement, self._page_sizes, minimum)
                paragraph_index = len(summaries)
                summaries.append(_PlannedParagraphSummary(
                    replacement, paragraph.text, paragraph.font_size,
                ))
                for placement in paragraph.placements:
                    self._record_font_size_statistic(headline_statistics, replacement, placement)
                storage.append(paragraph_index, replacement, paragraph)
                headline_replacements[paragraph_index] = replacement
                headline_minimums[paragraph_index] = minimum
            headline_targets = self._font_size_targets(headline_statistics)
            self._normalize_stored_placements(
                storage, headline_replacements, headline_targets, headline_minimums,
            )

            if body_font_sizes:
                latest_page = max(body_font_sizes)
                self._previous_body_font_size = body_font_sizes[latest_page]
            return storage.into_plan(first_page, last_page, tuple(summaries))
        except Exception:
            storage.close()
            raise

    @staticmethod
    def _record_font_size_statistic(
        statistics: dict[tuple[int, str, int], list[float]],
        replacement: PDFReplacement,
        placement: RegionTextPlacement,
    ) -> None:
        """Accumulate compact size statistics for a frozen placement."""
        text = placement.assigned_text or placement.remaining_text
        weight = len(text.strip())
        if not weight:
            return
        statistic = statistics.setdefault(
            (placement.page_index, replacement.layout_ref, replacement.layout_level),
            [0.0, 0.0, 0.0],
        )
        statistic[0] += placement.font_size * weight
        statistic[1] += weight
        statistic[2] += 1

    @staticmethod
    def _font_size_targets(
        statistics: Mapping[tuple[int, str, int], list[float]],
    ) -> dict[tuple[int, str, int], float]:
        """Return only averages supported by multiple bbox placements."""
        return {
            key: weighted_sizes / weights
            for key, (weighted_sizes, weights, placement_count) in statistics.items()
            if placement_count > 1 and weights > 0
        }

    def _normalize_stored_placements(
        self,
        storage: _WindowPlanStorage,
        replacements: Mapping[int, PDFReplacement],
        targets: Mapping[tuple[int, str, int], float],
        minimum_font_sizes: Mapping[int, float] | None = None,
    ) -> None:
        """Normalize serialized placements while keeping only one page live.

        First-pass paragraphs own inter-region flow.  This deliberately runs
        afterwards, grouping only placements that physically share a page and
        semantic level, then fitting each placement's already-consumed text
        independently.  No second-pass decision can move text across regions,
        and the page streams avoid retaining a long paragraph's full geometry.
        """
        if not targets:
            return

        def normalize(paragraph_index: int, placement: RegionTextPlacement) -> RegionTextPlacement:
            replacement = replacements.get(paragraph_index)
            if replacement is None:
                return placement
            target = targets.get((
                placement.page_index, replacement.layout_ref, replacement.layout_level,
            ))
            if target is None:
                return placement
            return self._filler.fit_frozen_region(
                placement, target, (minimum_font_sizes or {}).get(paragraph_index),
            )

        storage.rewrite_placements(normalize)

    @staticmethod
    def _stored_page_font_sizes(
        storage: _WindowPlanStorage,
        replacements: Mapping[int, PDFReplacement],
    ) -> dict[int, float]:
        """Read the normalized body stream back as compact per-page maxima."""
        font_sizes: dict[int, float] = {}
        for page_index in storage.page_indexes:
            for contribution in storage.page_contributions(page_index):
                if contribution.paragraph_index not in replacements:
                    continue
                for placement in contribution.placements:
                    font_sizes[placement.page_index] = max(
                        font_sizes.get(placement.page_index, 0.0), placement.font_size,
                    )
        return font_sizes

    def _fit_or_skip(
        self,
        replacement: PDFReplacement,
        obstacles: Mapping[int, tuple[PageRectangle, ...]],
    ) -> FittedParagraph | None:
        try:
            del obstacles
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
    if utf16_index <= 0:
        return 0
    units = 0
    for index, character in enumerate(text):
        units += 2 if ord(character) > 0xFFFF else 1
        if units >= utf16_index:
            return index + 1
    return len(text)


def _utf16_index_for_python(text: str, python_index: int) -> int:
    """Translate a Python character boundary to Qt's UTF-16 text position."""
    return len(text[:python_index].encode("utf-16-le")) // 2


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
