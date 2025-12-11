import sys
import os
import shutil

from typing import cast, runtime_checkable, Protocol
from os import PathLike
from pathlib import Path
from PIL import Image

from ..error import PDFError


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
    def open(self, pdf_path: Path) -> PDFDocument:
        ...


class DefaultPDFDocument:
    def __init__(self, pdf_path: Path, poppler_path: Path | None) -> None:
        self._pdf_path = pdf_path
        self._poppler_path: Path | None = poppler_path
        self._pages_count: int | None = None

    @property
    def pages_count(self) -> int:
        if self._pages_count is None:
            import pypdf
            with pypdf.PdfReader(str(self._pdf_path)) as reader:
                self._pages_count = len(reader.pages)
        return self._pages_count

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError

        poppler_path: str | None
        if self._poppler_path:
            poppler_path = str(self._poppler_path)
        else:
            poppler_path = None # use poppler in system PATH

        try:
            images: list[Image.Image] = convert_from_path(
                str(self._pdf_path),
                dpi=dpi,
                first_page=page_index,
                last_page=page_index,
                poppler_path=cast(str, poppler_path),
            )
        except PDFInfoNotInstalledError as error:
            if self._poppler_path:
                error_message = f"Poppler not found at specified path: {self._poppler_path}"
            else:
                error_message = "Poppler not found in PATH. Either not installed or PATH is not configured correctly."
            raise PDFError(error_message, page_index) from error

        if not images:
            raise RuntimeError(f"Failed to render page {page_index}")

        image = images[0]
        if image.mode != "RGB":
            image = image.convert("RGB")

        return image

    def close(self) -> None:
        pass


class DefaultPDFHandler:
    def __init__(self, poppler_path: PathLike | str | None = None) -> None:
        self._poppler_path: Path | None
        if poppler_path is not None:
            self._poppler_path = Path(poppler_path)
        else:
            self._poppler_path = _find_poppler_path()

    def open(self, pdf_path: Path) -> PDFDocument:
        return DefaultPDFDocument(
            pdf_path=pdf_path,
            poppler_path=self._poppler_path,
        )

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
