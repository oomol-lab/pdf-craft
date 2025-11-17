import fitz

from typing import cast, Generator
from os import PathLike
from pathlib import Path
from PIL.Image import frombytes

from ..common import AssetHub
from ..aborted import AbortedCheck
from .page_extractor import PageExtractorNode
from .types import Page, DeepSeekOCRModel


def pdf_pages_count(pdf_path: PathLike | str) -> int:
    with fitz.open(Path(pdf_path)) as document:
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
        self._document = fitz.open(self._pdf_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None

    def __iter__(self) -> Generator["PageRef", None, None]:
        document = cast(fitz.Document, self._document)
        for i in range(len(document)):
            yield PageRef(
                document=document,
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
            includes_footnotes: bool,
            includes_raw_image: bool,
            plot_path: Path | None,
            max_tokens: int | None,
            max_output_tokens: int | None,
        ) -> Page:

        dpi = 300 # for scanned book pages
        default_dpi = 72
        matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
        page = self._document.load_page(self._page_index - 1)
        pixmap = page.get_pixmap(matrix=matrix)
        image = frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

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
            aborted=self._aborted,
        )