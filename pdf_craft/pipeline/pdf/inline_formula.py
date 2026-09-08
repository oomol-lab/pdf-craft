"""Optional local TeX renderer for inline PDF formula fragments.

The renderer is deliberately best-effort.  Matplotlib is not a package
dependency and a working TeX executable is required; callers must always
retain the plain-text path below it.
"""

from dataclasses import dataclass
from io import BytesIO
from shutil import which
import subprocess


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
            else:
                latex = which("latex")
                if latex is None:
                    self._available = False
                else:
                    try:
                        subprocess.run([latex, "--version"], check=True, timeout=5,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except (OSError, subprocess.SubprocessError):
                        self._available = False
                    else:
                        self._available = True
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
            import matplotlib  # type: ignore[reportMissingImports]
            matplotlib.use("pdf", force=True)
            from matplotlib import pyplot as plt  # type: ignore[reportMissingImports]
            from matplotlib.texmanager import TexManager  # type: ignore[reportMissingImports]

            width, height, descent = TexManager().get_text_width_height_descent(
                f"${latex}$", point_size,
            )
            figure = plt.figure(figsize=(max(width, 1) / 72, max(height, 1) / 72))
            try:
                figure.patch.set_alpha(0)
                figure.text(0, descent / max(height, 1), f"${latex}$", usetex=True,
                            fontsize=point_size, color="black")
                output = BytesIO()
                figure.savefig(output, format="pdf", transparent=True,
                               bbox_inches="tight", pad_inches=0)
            finally:
                plt.close(figure)
            self._cache[key] = FormulaFragment(output.getvalue(), width, height, descent)
        except Exception:  # local TeX packages and individual expressions vary widely.
            self._cache[key] = None
        return self._cache[key]
