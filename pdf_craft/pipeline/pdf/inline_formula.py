"""Optional local TeX renderer for inline PDF formula fragments.

The renderer is deliberately best-effort.  Matplotlib is not a package
dependency and a working TeX executable is required; callers must always
retain the plain-text path below it.
"""

from dataclasses import dataclass
from io import BytesIO
from shutil import which



_TEX_PREAMBLE = r"\usepackage{amsfonts}"


@dataclass(frozen=True)
class FormulaFragment:
    """A cropped, transparent vector-PDF formula and its point metrics."""

    pdf: bytes
    width: float
    height: float
    descent: float


class InlineFormulaPDFRenderer:
    """Cache TeX-backed vector fragments by source and requested point size."""

    def __init__(self) -> None:
        self._available: bool | None = None
        self._probing = False
        self._cache: dict[tuple[str, float], FormulaFragment | None] = {}

    @property
    def available(self) -> bool:
        """Whether this machine can reasonably be asked to compile TeX."""
        if self._available is None:
            try:
                import matplotlib  # type: ignore[reportMissingImports]  # pylint: disable=import-outside-toplevel
                del matplotlib
            except ImportError:
                self._available = False
                return False
            self._available = which("latex") is not None
        if self._available and not self._probing:
            # Prove the complete Matplotlib -> TeX -> PDF path once.  A
            # present binary alone is not a usable rendering backend.
            self._probing = True
            try:
                if self.render(r"x", 8) is None:
                    self._available = False
            finally:
                self._probing = False
        return self._available

    def render(self, latex: str, point_size: float) -> FormulaFragment | None:
        """Return a vector PDF fragment, or ``None`` for one safe fallback."""
        key = (latex, point_size)
        if key in self._cache:
            return self._cache[key]
        if not self.available:
            self._cache[key] = None
            return None
        try:
            if which("latex") is None:
                raise RuntimeError("local TeX executable is unavailable")
            from matplotlib import rc_context  # type: ignore[reportMissingImports]
            from matplotlib.backends.backend_pdf import FigureCanvasPdf  # type: ignore[reportMissingImports]
            from matplotlib.figure import Figure  # type: ignore[reportMissingImports]
            from matplotlib.font_manager import FontProperties  # type: ignore[reportMissingImports]
            from matplotlib.patches import PathPatch  # type: ignore[reportMissingImports]
            from matplotlib.path import Path as VectorPath  # type: ignore[reportMissingImports]
            from matplotlib.textpath import TextToPath  # type: ignore[reportMissingImports]
            from matplotlib.texmanager import TexManager  # type: ignore[reportMissingImports]
            from matplotlib.transforms import Affine2D  # type: ignore[reportMissingImports]

            expression = f"${latex}$"
            # PCEX formulas originate from mathematical source documents, where
            # ``\mathbb`` is conventionally provided by amsfonts.  Matplotlib's
            # default usetex preamble is intentionally minimal, so make this
            # small, explicit baseline available to both measuring and drawing.
            # A machine without it still follows the normal safe fallback.
            with rc_context({
                "text.usetex": True,
                "font.family": "serif",
                "text.latex.preamble": _TEX_PREAMBLE,
            }):
                width, height, descent = TexManager().get_text_width_height_descent(
                    expression, point_size, None,
                )
                # TeX's optical sizes select different font programs. Embedding
                # a whole Type1 font for a five-glyph atom adds ~30KB per face
                # and size. Keep only its vector contours instead. ActualText
                # is added by the formula composition layer, not these glyphs.
                # Explicitly select the requested size inside TextToPath's
                # high-resolution DVI: its default 100pt design would otherwise
                # change optical glyphs and their metrics when scaled down.
                path_expression = rf"\fontsize{{{point_size}}}{{{1.25 * point_size}}}\selectfont {expression}"
                vertices, codes = TextToPath().get_text_path(
                    FontProperties(family="serif"), path_expression, ismath="TeX",
                )
                figure = Figure(figsize=(width / 72, height / 72))
                FigureCanvasPdf(figure)
                transform = Affine2D().translate(0, descent).scale(1 / 72) + figure.dpi_scale_trans
                figure.add_artist(PathPatch(
                    VectorPath(vertices, codes), facecolor="black", edgecolor="none", transform=transform,
                ))
                output = BytesIO()
                figure.savefig(output, format="pdf", transparent=True)
            self._cache[key] = FormulaFragment(output.getvalue(), float(width), float(height), float(descent))
        except Exception:  # local TeX packages and individual expressions vary widely.
            self._cache[key] = None
        return self._cache[key]
