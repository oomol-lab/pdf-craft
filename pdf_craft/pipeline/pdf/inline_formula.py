"""Optional local TeX renderer for inline PDF formula fragments.

The renderer is deliberately best-effort.  Matplotlib is not a package
dependency and a working TeX executable is required; callers must always
retain the plain-text path below it.
"""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from shutil import which
import subprocess
from tempfile import TemporaryDirectory


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
            latex = which("latex")
            if latex is None or not (which("dvipdfmx") or which("dvipdf")):
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
            latex_bin = which("latex")
            converter = which("dvipdfmx") or which("dvipdf")
            if latex_bin is None or converter is None:
                raise RuntimeError("local TeX PDF toolchain is unavailable")
            with TemporaryDirectory(prefix="pdf-craft-inline-tex-") as directory:
                root = Path(directory)
                source = root / "formula.tex"
                source.write_text(
                    "\\documentclass[preview]{standalone}\n"
                    "\\begin{document}\n"
                    f"{{\\fontsize{{{point_size}}}{{{point_size}}}\\selectfont ${latex}$}}\n"
                    "\\end{document}\n", encoding="utf-8",
                )
                subprocess.run(
                    [latex_bin, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", source.name],
                    cwd=root, check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                subprocess.run([converter, "formula.dvi"], cwd=root, check=True, timeout=15,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                output = root / "formula.pdf"
                pdf = output.read_bytes()
            # The standalone page is tight to the formula; its simple baseline
            # approximation is sufficient for the mixed-run planner.
            import pypdf
            page = pypdf.PdfReader(BytesIO(pdf)).pages[0]
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            self._cache[key] = FormulaFragment(pdf, width, height, height * 0.2)
        except Exception:  # local TeX packages and individual expressions vary widely.
            self._cache[key] = None
        return self._cache[key]
