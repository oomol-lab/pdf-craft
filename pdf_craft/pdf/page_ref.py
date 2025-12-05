import fitz

from typing import Generator
from os import PathLike
from pathlib import Path
from PIL.Image import frombytes

from ..common import AssetHub
from ..metering import AbortedCheck
from ..error import FitzError
from ..to_path import to_path
from .page_extractor import PageExtractorNode
from .types import Page, DeepSeekOCRModel


def pdf_pages_count(pdf_path: PathLike | str) -> int:
    with fitz.open(to_path(pdf_path)) as document:
        return len(document)


class PageRefContext:
    def __init__(
            self,
            pdf_path: Path,
            extractor: PageExtractorNode,
            asset_hub: AssetHub,
            aborted: AbortedCheck,
        ) -> None:
        self._pdf_path = pdf_path
        self._extractor = extractor
        self._asset_hub = asset_hub
        self._aborted: AbortedCheck = aborted
        self._document: fitz.Document | None = None

    @property
    def pages_count(self) -> int:
        assert self._document is not None
        return len(self._document)

    def __enter__(self) -> "PageRefContext":
        assert self._document is None
        try:
            self._document = fitz.open(self._pdf_path)
        except Exception as error:
            raise FitzError("Failed to open PDF document.") from error
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None

    def __iter__(self) -> Generator["PageRef", None, None]:
        assert self._document is not None
        for i in range(len(self._document)):
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
            document: fitz.Document,
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
            model: DeepSeekOCRModel,
            includes_footnotes: bool = False,
            includes_raw_image: bool = True,
            plot_path: Path | None = None,
            max_tokens: int | None = None,
            max_output_tokens: int | None = None,
            device_number: int | None = None,
        ) -> Page:

        try:
            dpi = 300 # for scanned book pages
            default_dpi = 72
            matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
            page = self._document.load_page(self._page_index - 1)
            pixmap = page.get_pixmap(matrix=matrix)
            image = frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        except Exception as error:
            raise FitzError(f"Failed to render page {self._page_index}.") from error

        return self._extractor.image2page(
            image=image,
            page_index=self._page_index,
            asset_hub=self._asset_hub,
            model_size=model,
            includes_footnotes=includes_footnotes,
            includes_raw_image=includes_raw_image,
            plot_path=plot_path,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
            device_number=device_number,
            aborted=self._aborted,
        )