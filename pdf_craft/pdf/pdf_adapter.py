"""
PDF Adapter Protocol

This module defines the abstract interface for PDF operations.
Users can implement their own adapter by conforming to this protocol.
"""

from typing import Protocol, runtime_checkable
from os import PathLike
from pathlib import Path
from PIL import Image


@runtime_checkable
class PDFDocument(Protocol):
    """Protocol for PDF document operations."""

    @property
    def pages_count(self) -> int:
        """Return the total number of pages in the document."""
        ...

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
        ...

    def close(self) -> None:
        """Close the PDF document and release resources."""
        ...


@runtime_checkable
class PDFAdapter(Protocol):
    """Protocol for PDF adapter that creates PDF documents."""

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
        ...

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
        ...
