import fitz
import re

from typing import Generator
from pathlib import Path
from PIL.Image import frombytes, Image
from doc_page_extractor import PageExtractor, DeepSeekOCRSize

from .asset import AssetHub
from .page import ASSET_TAGS, Page, PageLayout


class Extractor:
    def __init__(self, asset_hub: AssetHub) -> None:
        self._page_extractor = PageExtractor()
        self._asset_hub = asset_hub

    def extract(
            self, 
            pdf_path: Path, 
            model_size: DeepSeekOCRSize,
            includes_footnotes: bool,
        ) -> Generator[Page, None, None]:

        with fitz.open(pdf_path) as document:
            for page_index in range(len(document)):
                page = document.load_page(page_index)
                image = self._page_screenshot_image(page)
                yield self._convert_to_page(
                    page_index=page_index,
                    image=image, 
                    model_size=model_size, 
                    includes_footnotes=includes_footnotes,
                )

    def _page_screenshot_image(self, page: fitz.Page):
        dpi = 300 # for scanned book pages
        default_dpi = 72
        matrix = fitz.Matrix(dpi / default_dpi, dpi / default_dpi)
        pixmap = page.get_pixmap(matrix=matrix)
        return frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    
    def _convert_to_page(
            self,
            page_index: int,
            image: Image, 
            model_size: DeepSeekOCRSize, 
            includes_footnotes: bool,
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

        return Page(
            index=page_index,
            body_layouts=body_layouts,
            footnotes_layouts=footnotes_layouts,
        )
    
    def _normalize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()