from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from PIL import Image

from ..error import PDFError
from .types import PDFDocumentMetadata


@runtime_checkable
class PDFDocument(Protocol):
    @property
    def pages_count(self) -> int: ...

    def metadata(self) -> PDFDocumentMetadata: ...

    def page_size(self, page_index: int) -> tuple[float, float]: ...

    def render_page(self, page_index: int, dpi: int) -> Image.Image: ...

    def close(self) -> None: ...


@runtime_checkable
class PDFHandler(Protocol):
    def open(self, pdf_path: Path) -> PDFDocument: ...


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


_POINTS_PER_INCH = 72.0


class DefaultPDFDocument:
    def __init__(self, pdf_path: Path, poppler_path: Path | None) -> None:
        import pypdf

        self._pdf_path = pdf_path
        self._poppler_path: Path | None = poppler_path
        self._reader = pypdf.PdfReader(str(pdf_path))
        self._pages_count: int | None = None

    @property
    def pages_count(self) -> int:
        if self._pages_count is None:
            self._pages_count = len(self._reader.pages)
        return self._pages_count

    def metadata(self) -> PDFDocumentMetadata:
        try:
            metadata = self._reader.metadata
            title = (
                str(metadata.get("/Title"))
                if metadata and metadata.get("/Title")
                else None
            )
            description = (
                str(metadata.get("/Subject"))
                if metadata and metadata.get("/Subject")
                else None
            )
            authors: list[str] = []

            if metadata and "/Author" in metadata:
                author_obj = metadata["/Author"]
                if author_obj:
                    author_str = str(author_obj)
                    for sep in (";", ",", "&"):
                        if sep in author_str:
                            authors = [
                                a.strip() for a in author_str.split(sep) if a.strip()
                            ]
                            break
                    if not authors:
                        authors = [author_str.strip()]

            modified = datetime.now(timezone.utc)
            if metadata and "/ModDate" in metadata:
                try:
                    mod_date_obj = metadata["/ModDate"]
                    if mod_date_obj:
                        mod_date_str = str(mod_date_obj)
                        # PDF 日期格式: D:YYYYMMDDHHmmSSOHH'mm'
                        if mod_date_str.startswith("D:"):
                            mod_date_str = mod_date_str[2:]
                        # 简化处理：只取年月日时分秒
                        if len(mod_date_str) >= 14:
                            year = int(mod_date_str[0:4])
                            month = int(mod_date_str[4:6])
                            day = int(mod_date_str[6:8])
                            hour = int(mod_date_str[8:10])
                            minute = int(mod_date_str[10:12])
                            second = int(mod_date_str[12:14])
                            modified = datetime(
                                year,
                                month,
                                day,
                                hour,
                                minute,
                                second,
                                tzinfo=timezone.utc,
                            )
                except (ValueError, IndexError):
                    pass  # 解析失败则使用当前时间

            return PDFDocumentMetadata(
                title=title,
                description=description,
                publisher=None,  # PDF 标准元数据中通常没有 publisher
                isbn=None,  # PDF 标准元数据中通常没有 ISBN
                authors=authors,
                editors=[],  # PDF 标准元数据中通常没有 editors
                translators=[],  # PDF 标准元数据中通常没有 translators
                modified=modified,
            )
        except Exception as error:
            raise PDFError(
                "Failed to extract PDF metadata.", page_index=None
            ) from error

    def page_size(self, page_index: int) -> tuple[float, float]:
        try:
            page = self._reader.pages[page_index - 1]
            width_inch = float(page.mediabox.width) / _POINTS_PER_INCH
            height_inch = float(page.mediabox.height) / _POINTS_PER_INCH
            return (width_inch, height_inch)
        except Exception as error:
            raise PDFError(
                f"Failed to get page size for page {page_index}.", page_index=page_index
            ) from error

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        from pdf2image import convert_from_path
        from pdf2image.exceptions import PDFInfoNotInstalledError

        poppler_path: str | None
        if self._poppler_path:
            poppler_path = str(self._poppler_path)
        else:
            poppler_path = None  # use poppler in system PATH

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
                error_message = (
                    f"Poppler not found at specified path: {self._poppler_path}"
                )
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
        self._reader.stream.close()
