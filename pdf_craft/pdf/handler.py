import sys
import os
import shutil
import pypdf

from typing import Protocol, runtime_checkable
from os import PathLike
from pathlib import Path
from PIL import Image


@runtime_checkable
class PDFDocument(Protocol):
    @property
    def pages_count(self) -> int:
        ...

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class PDFHandler(Protocol):
    def open(self, pdf_path: PathLike | str) -> PDFDocument:
        ...

    def get_pages_count(self, pdf_path: PathLike | str) -> int:
        ...


class DefaultPDFDocument:
    def __init__(self, pdf_path: Path, poppler_path: Path | None = None) -> None:
        self._pdf_path = pdf_path
        self._poppler_path: Path | None = poppler_path
        self._reader: pypdf.PdfReader | None = None
        self._pages_count: int = 0
        self._open()

    def _open(self) -> None:
        self._reader = pypdf.PdfReader(str(self._pdf_path))
        self._pages_count = len(self._reader.pages)

    @property
    def pages_count(self) -> int:
        return self._pages_count

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        from pdf2image import convert_from_path
        if page_index < 0 or page_index >= self._pages_count:
            raise IndexError(
                f"Page index {page_index} out of range [0, {self._pages_count})"
            )

        # pdf2image uses 1-based page numbers
        images: list[Image.Image] = convert_from_path(
            str(self._pdf_path),
            dpi=dpi,
            first_page=page_index + 1,
            last_page=page_index + 1,
            # Note: pdf2image's type hint is wrong - it accepts None despite annotation
            poppler_path=str(self._poppler_path) if self._poppler_path else None,  # type: ignore[arg-type]
        )

        if not images:
            raise RuntimeError(f"Failed to render page {page_index}")

        image = images[0]

        # Ensure RGB mode
        if image.mode != "RGB":
            image = image.convert("RGB")

        return image

    def close(self) -> None:
        if self._reader is not None:
            # pypdf doesn't require explicit close in newer versions
            # but we keep this for protocol compliance
            self._reader = None


class DefaultPDFHandler:
    def __init__(self, poppler_path: PathLike | str | None = None) -> None:
        self._poppler_path: Path | None
        if poppler_path is not None:
            self._poppler_path = Path(poppler_path)
        else:
            self._poppler_path = _find_poppler_path()

    def open(self, pdf_path: PathLike | str) -> PDFDocument:
        return DefaultPDFDocument(
            pdf_path=Path(pdf_path),
            poppler_path=self._poppler_path,
        )

    def get_pages_count(self, pdf_path: PathLike | str) -> int:
        path = Path(pdf_path) if not isinstance(pdf_path, Path) else pdf_path
        reader = pypdf.PdfReader(str(path))
        return len(reader.pages)

def _find_poppler_path() -> Path | None:
    # Check if pdfinfo is already in PATH
    if shutil.which("pdfinfo"):
        return None  # Already in PATH, no need to specify

    # Platform-specific search paths
    possible_paths: list[Path] = []

    if sys.platform == "darwin":  # macOS
        possible_paths.extend([
            Path("/opt/homebrew/bin"),  # Homebrew on Apple Silicon
            Path("/usr/local/bin"),  # Homebrew on Intel Mac
            Path("/opt/homebrew/Cellar/poppler"),  # Homebrew Cellar on Apple Silicon
            Path("/usr/local/Cellar/poppler"),  # Homebrew Cellar on Intel
        ])
    elif sys.platform.startswith("linux"):  # Linux
        possible_paths.extend([
            Path("/usr/bin"),  # apt/yum install poppler-utils
            Path("/usr/local/bin"),  # Manual installation
            Path("/snap/bin"),  # Snap package
        ])
    elif sys.platform == "win32":  # Windows
        # Check common Windows installation paths
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")

        possible_paths.extend([
            Path(program_files) / "poppler" / "Library" / "bin",
            Path(program_files_x86) / "poppler" / "Library" / "bin",
            Path(program_files) / "poppler-utils" / "bin",
            Path(program_files_x86) / "poppler-utils" / "bin",
        ])
        if local_app_data:
            possible_paths.append(Path(local_app_data) / "poppler" / "Library" / "bin")

    # Search for poppler in candidate paths
    for base_path in possible_paths:
        if not base_path.exists():
            continue

        # For Cellar paths (macOS Homebrew), find the version directory
        if base_path.name == "poppler":
            try:
                version_dirs = sorted(base_path.iterdir(), reverse=True)
                for version_dir in version_dirs:
                    if not version_dir.is_dir():
                        continue
                    bin_path = version_dir / "bin"
                    if bin_path.exists() and (bin_path / "pdfinfo").exists():
                        return bin_path
            except (OSError, PermissionError):
                continue
        else:
            # Direct bin path - check for pdfinfo or pdfinfo.exe
            pdfinfo_path = base_path / "pdfinfo"
            pdfinfo_exe_path = base_path / "pdfinfo.exe"
            if pdfinfo_path.exists() or pdfinfo_exe_path.exists():
                return base_path

    return None
