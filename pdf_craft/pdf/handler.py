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
        self._poppler_path: Path | None = None
        if poppler_path is not None:
            self._poppler_path = Path(poppler_path)

    def open(self, pdf_path: Path) -> PDFDocument:
        return DefaultPDFDocument(
            pdf_path=pdf_path,
            poppler_path=self._poppler_path,
        )