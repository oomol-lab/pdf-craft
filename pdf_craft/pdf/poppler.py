"""Read-only Poppler adapter. PDF object editing belongs to the patcher."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import logging
from pathlib import Path
import re

from PIL import Image

from ..runtime import run_subprocess_sync
from .types import PDFDocumentMetadata


_LOGGER = logging.getLogger(__name__)
# Poppler's ErrorCategory distinguishes workarounds that probably preserve
# output (Syntax Warning) from errors that can leave incomplete page content.
# Its tools can emit these error categories and still return exit status zero.
_ERROR_DIAGNOSTIC = re.compile(
    r"^\s*(?:Syntax Error|Config Error|Command Line Error|I/O Error|Permission Error|"
    r"Unimplemented Feature|Internal Error|Error)(?:\s+\([^\r\n)]*\))?:",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PageGeometry:
    """PDF-point boxes before rotation; rasterization uses the MediaBox."""

    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float]
    rotation: int

    @property
    def size(self) -> tuple[float, float]:
        left, bottom, right, top = self.media_box
        width, height = right - left, top - bottom
        return (height, width) if self.rotation % 180 else (width, height)


class PopplerDocument:
    """Small CLI boundary with explicit process ownership and cancellation."""

    def __init__(self, source: Path, poppler_path: Path | None = None,
                 aborted: Callable[[], bool] | None = None) -> None:
        self.source = source.resolve()
        self.poppler_path = poppler_path
        self.aborted = aborted
        self._info: dict[str, str] | None = None
        self._geometries: dict[int, PageGeometry] = {}

    def command(self, name: str) -> str:
        return str(self.poppler_path / name) if self.poppler_path else name

    def _run(self, *arguments: str) -> bytes:
        output, stderr = run_subprocess_sync(
            *arguments, aborted=self.aborted,
        )
        diagnostic = stderr.decode("utf-8", errors="replace").strip()
        if _ERROR_DIAGNOSTIC.search(diagnostic):
            raise RuntimeError(f"Poppler {Path(arguments[0]).name} reported an error: {diagnostic}")
        if diagnostic:
            _LOGGER.warning("Poppler %s: %s", Path(arguments[0]).name, diagnostic)
        return output

    @property
    def info(self) -> dict[str, str]:
        if self._info is None:
            output = self._run(self.command("pdfinfo"), "-enc", "UTF-8", "-isodates", str(self.source))
            self._info = {
                key.strip(): value.strip() for line in output.decode("utf-8").splitlines()
                if ":" in line for key, value in [line.split(":", 1)]
            }
        return self._info

    @property
    def pages_count(self) -> int:
        count = int(self.info["Pages"])
        if count <= 0:
            raise ValueError("PDF has no pages")
        return count

    def geometry(self, page_index: int) -> PageGeometry:
        if not 1 <= page_index <= self.pages_count:
            raise IndexError(f"PDF page {page_index} is out of range")
        if page_index not in self._geometries:
            output = self._run(self.command("pdfinfo"), "-f", str(page_index), "-l", str(page_index),
                               "-box", str(self.source)).decode("utf-8")
            boxes = {}
            for name in ("MediaBox", "CropBox"):
                match = re.search(rf"(?:Page\s+\d+\s+)?{name}:\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", output)
                if match is None:
                    raise ValueError(f"Poppler did not report {name} for page {page_index}")
                boxes[name] = tuple(float(value) for value in match.groups())
            rotation = re.search(r"(?:Page\s+\d+\s+)?(?:Page\s+)?rot:\s+(-?\d+)", output)
            if rotation is None:
                raise ValueError(f"Poppler did not report rotation for page {page_index}")
            self._geometries[page_index] = PageGeometry(
                boxes["MediaBox"], boxes["CropBox"], int(rotation[1]) % 360,
            )
        return self._geometries[page_index]

    def metadata(self) -> PDFDocumentMetadata:
        info = self.info
        author = info.get("Author", "")
        authors = [author.strip()] if author.strip() else []
        for separator in (";", ",", "&"):
            if separator in author:
                authors = [part.strip() for part in author.split(separator) if part.strip()]
                break
        try:
            modified = datetime.fromisoformat(info.get("ModDate", ""))
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
        except ValueError:
            modified = datetime.now(timezone.utc)
        return PDFDocumentMetadata(
            title=info.get("Title") or None, description=info.get("Subject") or None,
            publisher=None, isbn=None, authors=authors, editors=[], translators=[], modified=modified,
        )

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        if dpi <= 0 or not 1 <= page_index <= self.pages_count:
            raise ValueError("Invalid PDF page or rendering DPI")
        output = self._run(self.command("pdftoppm"), "-f", str(page_index), "-l", str(page_index),
                           "-singlefile", "-r", str(dpi), str(self.source))
        with Image.open(BytesIO(output)) as image:
            return image.convert("RGB")

    def close(self) -> None:
        """CLI processes and image buffers are released by each operation."""
