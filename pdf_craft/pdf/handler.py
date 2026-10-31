from dataclasses import dataclass
from collections.abc import Container
import inspect
from ..runtime import IO_DOMAIN
from os import PathLike
from pathlib import Path
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class AsyncPDFDocument(Protocol):
    """Awaitable PDF document contract for async SDK extensions."""

    async def pages_count(self) -> int: ...

    async def metadata(self) -> PDFDocumentMetadata: ...

    async def page_size(self, page_index: int) -> tuple[float, float]: ...

    async def render_page(self, page_index: int, dpi: int) -> Image.Image: ...

    async def close(self) -> None: ...


@runtime_checkable
class AsyncPDFHandler(Protocol):
    """Native async PDF provider accepted by :class:`AsyncPDFCraft`."""

    async def open(self, pdf_path: Path) -> AsyncPDFDocument: ...


class DefaultPDFHandler:
    def __init__(self, poppler_path: PathLike | str | None = None) -> None:
        self._poppler_path: Path | None = None
        if poppler_path is not None:
            self._poppler_path = Path(poppler_path)

    @property
    def poppler_path(self) -> Path | None:
        """Explicit Poppler executable directory, shared with nested readers."""
        return self._poppler_path

    def open(self, pdf_path: Path) -> PDFDocument:
        from .backend import current_poppler_path
        return DefaultPDFDocument(
            pdf_path=pdf_path,
            poppler_path=self._poppler_path or current_poppler_path(),
        )


_POINTS_PER_INCH = 72.0


class DefaultPDFDocument:
    """Read-only default provider backed entirely by Poppler."""

    def __init__(self, pdf_path: Path, poppler_path: Path | None) -> None:
        from .poppler import PopplerDocument
        self._document = PopplerDocument(pdf_path, poppler_path)

    @property
    def pages_count(self) -> int:
        return self._document.pages_count

    def metadata(self) -> PDFDocumentMetadata:
        try:
            return self._document.metadata()
        except Exception as error:
            raise PDFError("Failed to extract PDF metadata.") from error

    def page_size(self, page_index: int) -> tuple[float, float]:
        try:
            width, height = self._document.geometry(page_index).size
            return width / _POINTS_PER_INCH, height / _POINTS_PER_INCH
        except Exception as error:
            raise PDFError(f"Failed to get page size for page {page_index}.", page_index) from error

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        try:
            return self._document.render_page(page_index, dpi)
        except Exception as error:
            raise PDFError(f"Failed to render page {page_index}.", page_index) from error

    def close(self) -> None:
        self._document.close()


@dataclass(frozen=True)
class _MaterializedPDFHandler:
    """Serializable PDF handler whose page rasters live in a temp workspace."""

    root: Path
    count: int
    document_metadata: PDFDocumentMetadata
    page_sizes: dict[int, tuple[float, float]]
    dpi: int | dict[int, int]

    def open(self, pdf_path: Path) -> PDFDocument:
        del pdf_path
        return _MaterializedPDFDocument(
            self.root, self.count, self.document_metadata, self.page_sizes, self.dpi,
        )


class _MaterializedPDFDocument:
    def __init__(
        self,
        root: Path,
        count: int,
        document_metadata: PDFDocumentMetadata,
        page_sizes: dict[int, tuple[float, float]],
        dpi: int | dict[int, int],
    ) -> None:
        self._root = root
        self._count = count
        self._metadata = document_metadata
        self._page_sizes = page_sizes
        self._dpi = dpi

    @property
    def pages_count(self) -> int:
        return self._count

    def metadata(self) -> PDFDocumentMetadata:
        return self._metadata

    def page_size(self, page_index: int) -> tuple[float, float]:
        return self._page_sizes[page_index]

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        expected_dpi = self._dpi[page_index] if isinstance(self._dpi, dict) else self._dpi
        if dpi <= 0 or dpi > expected_dpi:
            raise ValueError(
                f"materialized PDF page uses {expected_dpi} DPI, requested {dpi} DPI"
            )
        image = Image.open(self._root / f"page-{page_index}.png")
        try:
            image.load()
            if dpi == expected_dpi:
                return image
            # Async handlers have already supplied the source raster. Retry
            # from that raster, never from a previously downsized candidate.
            resized = image.resize((max(1, round(image.width * dpi / expected_dpi)),
                                    max(1, round(image.height * dpi / expected_dpi))))
        except BaseException:
            image.close()
            raise
        image.close()
        return resized

    def close(self) -> None:
        return None


async def _materialize_pdf_handler(
    handler: AsyncPDFHandler,
    source: Path,
    page_indexes: Container[int] | None,
    dpi: int | dict[int, int],
    root: Path,
    *, on_metadata=None,
) -> _MaterializedPDFHandler:
    if not inspect.iscoroutinefunction(handler.open):
        raise TypeError(
            "pdf_handler must implement async def open()"
        )
    document = await handler.open(source)
    try:
        count = await document.pages_count()
        metadata = await document.metadata()
        if on_metadata is not None:
            await on_metadata(metadata)
        page_sizes = {
            page_index: await document.page_size(page_index)
            for page_index in range(1, count + 1)
        }
        selected_pages = (
            (index for index in range(1, count + 1)
             if page_indexes is None or index in page_indexes)
        )
        for page_index in selected_pages:
            if not 1 <= page_index <= count:
                continue
            page_dpi = dpi[page_index] if isinstance(dpi, dict) else dpi
            from ..extractor.progress import page_stage
            await page_stage(page_index, 0)
            image = await document.render_page(page_index, page_dpi)
            await IO_DOMAIN.run(
                _save_and_close_page_image, image, root / f"page-{page_index}.png",
            )
    finally:
        await document.close()
    return _MaterializedPDFHandler(root, count, metadata, page_sizes, dpi)


def _save_and_close_page_image(image: Image.Image, path: Path) -> None:
    try:
        image.save(path, format="PNG")
    finally:
        image.close()
