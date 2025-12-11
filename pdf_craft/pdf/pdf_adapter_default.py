"""
Default PDF Adapter Implementation

Uses pdf2image (MIT) and pypdf (BSD-3-Clause) for PDF operations.
Both libraries are compatible with MIT license.
"""

import os
import shutil
from typing import List
from os import PathLike
from pathlib import Path
from PIL import Image
import pypdf
from pdf2image import convert_from_path

from .pdf_adapter import PDFAdapter, PDFDocument


def _find_poppler_path() -> Path | None:
    """
    Find poppler installation path.

    Returns:
        Path to poppler bin directory if found, None otherwise
    """
    # Check if pdfinfo is in PATH
    if shutil.which("pdfinfo"):
        return None  # Already in PATH, no need to specify

    # Check common installation locations
    possible_paths = [
        Path("/opt/homebrew/bin"),  # Homebrew on Apple Silicon
        Path("/usr/local/bin"),  # Homebrew on Intel Mac
        Path("/opt/homebrew/Cellar/poppler"),  # Homebrew Cellar on Apple Silicon
        Path("/usr/local/Cellar/poppler"),  # Homebrew Cellar on Intel
    ]

    for base_path in possible_paths:
        if not base_path.exists():
            continue

        # For Cellar paths, find the version directory
        if base_path.name == "poppler":
            try:
                version_dirs = sorted(base_path.iterdir(), reverse=True)
                for version_dir in version_dirs:
                    bin_path = version_dir / "bin"
                    if bin_path.exists() and (bin_path / "pdfinfo").exists():
                        return bin_path
            except (OSError, PermissionError):
                continue
        else:
            # Direct bin path
            if (base_path / "pdfinfo").exists():
                return base_path

    return None


class DefaultPDFDocument:
    """Default implementation of PDFDocument using pdf2image and pypdf."""

    def __init__(self, pdf_path: Path, poppler_path: Path | None = None) -> None:
        self._pdf_path = pdf_path
        self._poppler_path = poppler_path
        self._reader: pypdf.PdfReader | None = None
        self._pages_count: int = 0
        self._open()

    def _open(self) -> None:
        """Open the PDF file and read metadata."""
        self._reader = pypdf.PdfReader(str(self._pdf_path))
        self._pages_count = len(self._reader.pages)

    @property
    def pages_count(self) -> int:
        """Return the total number of pages in the document."""
        return self._pages_count

    def render_page(self, page_index: int, dpi: int = 300) -> Image.Image:
        """
        Render a specific page to a PIL Image.

        Args:
            page_index: 0-based page index
            dpi: Dots per inch for rendering (default: 300)

        Returns:
            PIL Image in RGB mode

        Raises:
            Exception: If rendering fails
        """
        if page_index < 0 or page_index >= self._pages_count:
            raise IndexError(
                f"Page index {page_index} out of range [0, {self._pages_count})"
            )

        # pdf2image uses 1-based page numbers
        images: List[Image.Image] = convert_from_path(
            str(self._pdf_path),
            dpi=dpi,
            first_page=page_index + 1,
            last_page=page_index + 1,
            poppler_path=str(self._poppler_path) if self._poppler_path else None,
        )

        if not images:
            raise RuntimeError(f"Failed to render page {page_index}")

        image = images[0]

        # Ensure RGB mode
        if image.mode != "RGB":
            image = image.convert("RGB")

        return image

    def close(self) -> None:
        """Close the PDF document and release resources."""
        if self._reader is not None:
            # pypdf doesn't require explicit close in newer versions
            # but we keep this for protocol compliance
            self._reader = None


class DefaultPDFAdapter:
    """Default implementation of PDFAdapter using pdf2image and pypdf."""

    def __init__(self, poppler_path: Path | str | None = None) -> None:
        """
        Initialize the adapter.

        Args:
            poppler_path: Path to poppler bin directory. If None, will auto-detect.
        """
        if poppler_path is not None:
            self._poppler_path: Path | None = (
                Path(poppler_path) if not isinstance(poppler_path, Path) else poppler_path
            )
        else:
            self._poppler_path = _find_poppler_path()

    def open(self, pdf_path: PathLike | str) -> PDFDocument:
        """
        Open a PDF file and return a document object.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            PDFDocument instance

        Raises:
            Exception: If the PDF cannot be opened
        """
        path = Path(pdf_path) if not isinstance(pdf_path, Path) else pdf_path
        return DefaultPDFDocument(path, self._poppler_path)

    def get_pages_count(self, pdf_path: PathLike | str) -> int:
        """
        Get the number of pages in a PDF file without fully loading it.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Number of pages

        Raises:
            Exception: If the PDF cannot be read
        """
        path = Path(pdf_path) if not isinstance(pdf_path, Path) else pdf_path
        reader = pypdf.PdfReader(str(path))
        return len(reader.pages)
