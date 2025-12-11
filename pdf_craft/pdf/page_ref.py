from typing import Generator
from os import PathLike
from pathlib import Path
from typing import Callable

from ..common import AssetHub
from ..metering import AbortedCheck
from ..error import PDFError
from .page_extractor import PageExtractorNode
from .types import Page, DeepSeekOCRSize
from .handler import PDFHandler, PDFDocument, DefaultPDFHandler


def pdf_pages_count(
        pdf_path: PathLike | str,
        pdf_handler: PDFHandler | None = None,
    ) -> int:
    if pdf_handler is None:
        pdf_handler = DefaultPDFHandler()
    document: PDFDocument | None = None
    try:
        document = pdf_handler.open(pdf_path=Path(pdf_path))
        return document.pages_count
    except PDFError:
        raise
    except Exception as error:
        raise PDFError("Failed to parse PDF document.", page_index=None) from error
    finally:
        if document is not None:
            document.close()


class PageRefContext:
    def __init__(
            self,
            pdf_path: Path,
            pdf_handler: PDFHandler | None,
            extractor: PageExtractorNode,
            asset_hub: AssetHub,
            aborted: AbortedCheck,
        ) -> None:
        self._pdf_path = pdf_path
        self._pdf_handler: PDFHandler = pdf_handler if pdf_handler is not None else DefaultPDFHandler()
        self._extractor = extractor
        self._asset_hub = asset_hub
        self._aborted: AbortedCheck = aborted
        self._document: PDFDocument | None = None

    @property
    def pages_count(self) -> int:
        assert self._document is not None
        return self._document.pages_count

    def __enter__(self) -> "PageRefContext":
        assert self._document is None
        try:
            self._document = self._pdf_handler.open(self._pdf_path)
        except PDFError:
            raise
        except Exception as error:
            raise PDFError("Failed to open PDF document.", page_index=None) from error
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None

    def __iter__(self) -> Generator["PageRef", None, None]:
        assert self._document is not None
        for i in range(self._document.pages_count):
            yield PageRef(
                document=self._document,
                page_index=i + 1,
                extractor=self._extractor,
                asset_hub=self._asset_hub,
                aborted=self._aborted,
            )

class PageRef:
    def __init__(
            self,
            document: PDFDocument,
            page_index: int,
            extractor: PageExtractorNode,
            asset_hub: AssetHub,
            aborted: AbortedCheck,
        ) -> None:
        self._document = document
        self._page_index = page_index
        self._extractor = extractor
        self._asset_hub = asset_hub
        self._aborted: AbortedCheck = aborted

    @property
    def page_index(self) -> int:
        return self._page_index

    def extract(
            self,
            ocr_size: DeepSeekOCRSize,
            includes_footnotes: bool,
            includes_raw_image: bool,
            plot_path: Path | None,
            max_tokens: int | None,
            max_output_tokens: int | None,
            device_number: int | None,
        ) -> Page:

        try:
            image = self._document.render_page(
                page_index=self._page_index,
                dpi=300, # Render page at 300 DPI for scanned book pages
            )
        except PDFError as error:
            error.page_index = self._page_index
            raise error

        except Exception as error:
            raise PDFError(f"Failed to render page {self._page_index}.", page_index=self._page_index) from error

        return self._extractor.image2page(
            image=image,
            page_index=self._page_index,
            asset_hub=self._asset_hub,
            model_size=ocr_size,
            includes_footnotes=includes_footnotes,
            includes_raw_image=includes_raw_image,
            plot_path=plot_path,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
            device_number=device_number,
            aborted=self._aborted,
        )
