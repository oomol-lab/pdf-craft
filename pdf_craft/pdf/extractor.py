import fitz
import re

from typing import cast, Generator
from pathlib import Path
from PIL.Image import frombytes, Image
from doc_page_extractor import plot, PageExtractor, DeepSeekOCRSize

from ..asset import ASSET_TAGS, AssetHub
from .page import Page, PageLayout


class Extractor:
    def __init__(self, asset_hub: AssetHub) -> None:
        self._page_extractor = PageExtractor()
        self._asset_hub = asset_hub

    def page_refs(self, pdf_path: Path) -> "PageRefContext":
        return PageRefContext(
            pdf_path=pdf_path,
            page_extractor=self._page_extractor,
            asset_hub=self._asset_hub,
        )

class PageRefContext:
    def __init__(
            self,
            pdf_path: Path,
            page_extractor: PageExtractor,
            asset_hub: AssetHub,
        ) -> None:
        self._pdf_path = pdf_path
        self._page_extractor = page_extractor
        self._asset_hub = asset_hub
        self._document = None

    def __enter__(self) -> Generator["PageRef", None, None]:
        assert self._document is None
        self._document = fitz.open(self._pdf_path)
        return self._generate_page_refs()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._document is not None:
            self._document.close()
            self._document = None

    def _generate_page_refs(self) -> Generator["PageRef", None, None]:
        document = cast(fitz.Document, self._document)
        for i in range(len(document)):
            yield PageRef(
                document=document,
                page_index=i + 1,
                page_extractor=self._page_extractor,
                asset_hub=self._asset_hub,
            )

class PageRef:
    def __init__(
            self,
            document: fitz.Document,
            page_index: int,
            page_extractor: PageExtractor,
            asset_hub: AssetHub,
        ) -> None:
        self._document = document
        self._page_index = page_index
        self._page_extractor = page_extractor
        self._asset_hub = asset_hub

    @property
    def page_index(self) -> int:
        return self._page_index

    def extract(
            self,
            model_size: DeepSeekOCRSize,
            includes_footnotes: bool,
            plot_path: Path | None,
        ) -> Page:
        dpi = 300 # for scanned book pages
        default_dpi = 72
        matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
        page = self._document.load_page(self._page_index - 1)
        pixmap = page.get_pixmap(matrix=matrix)
        image = frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return self._convert_to_page(
            image=image,
            model_size=model_size,
            includes_footnotes=includes_footnotes,
            plot_path=plot_path,
        )

    def _convert_to_page(
            self,
            image: Image,
            model_size: DeepSeekOCRSize,
            includes_footnotes: bool,
            plot_path: Path | None,
        ) -> Page:
        body_layouts: list[PageLayout] = []
        footnotes_layouts: list[PageLayout] = []

        for i, (image, layouts) in enumerate(self._page_extractor.extract(
            image=image,
            size=model_size,
            stages=2 if includes_footnotes else 1,
        )):
            for layout in layouts:
                ref = self._normalize_text(layout.ref)
                text = self._normalize_text(layout.text)
                hash: str | None = None
                if ref in ASSET_TAGS and i == 0:
                    hash = self._asset_hub.clip(image, layout.det)
                page_layout = PageLayout(
                    ref=ref,
                    det=layout.det,
                    text=text,
                    hash=hash,
                )
                if i == 0:
                    body_layouts.append(page_layout)
                elif i == 1 and ref not in ASSET_TAGS:
                    footnotes_layouts.append(page_layout)

            if plot_path is not None:
                plot_file_path = plot_path / f"page_{self._page_index}_stage_{i}.png"
                image = plot(image.copy(), layouts)
                image.save(plot_file_path, format="PNG")

        return Page(
            index=self._page_index,
            body_layouts=body_layouts,
            footnotes_layouts=footnotes_layouts,
        )

    def _normalize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()