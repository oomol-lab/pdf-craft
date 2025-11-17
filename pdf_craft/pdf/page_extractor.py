import re

from pathlib import Path
from os import PathLike
from PIL.Image import Image

from ..common import ASSET_TAGS, AssetHub
from ..errors import check_aborted, AbortedCheck
from .types import Page, PageLayout, DeepSeekOCRModel


class PageExtractorNode:
    def __init__(
        self,
        model_path: PathLike | None = None,
        local_only: bool = False,
    ) -> None:
        self._model_path: PathLike | None = model_path
        self._local_only: bool = local_only

    def _page_extractor(self):
        # 尽可能推迟 doc-page-extractor 的加载时间
        from doc_page_extractor import PageExtractor
        return PageExtractor(
            model_path=self._model_path,
            local_only=self._local_only,
        )

    def download_models(self) -> None:
        self._page_extractor().download_models()

    def load_models(self) -> None:
        self._page_extractor().load_models()

    def image2page(
            self,
            image: Image,
            page_index: int,
            asset_hub: AssetHub,
            model_size: DeepSeekOCRModel,
            includes_footnotes: bool,
            includes_raw_image: bool,
            plot_path: Path | None,
            max_tokens: int | None,
            max_output_tokens: int | None,
            aborted: AbortedCheck,
        ) -> Page:

        from doc_page_extractor import plot, ExtractionContext
        body_layouts: list[PageLayout] = []
        footnotes_layouts: list[PageLayout] = []
        raw_image: Image | None = None

        if includes_raw_image:
            raw_image = image
            image = image.copy()

        context = ExtractionContext(
            check_aborted=aborted,
            max_tokens=max_tokens,
            max_output_tokens=max_output_tokens,
        )
        for i, (image, layouts) in enumerate(self._page_extractor().extract(
            image=image,
            size=model_size,
            stages=2 if includes_footnotes else 1,
            context=context,
        )):
            for layout in layouts:
                ref = self._normalize_text(layout.ref)
                text = self._normalize_text(layout.text)
                hash: str | None = None
                if ref in ASSET_TAGS:
                    hash = asset_hub.clip(image, layout.det)
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

            check_aborted(aborted)
            if plot_path is not None:
                plot_file_path = plot_path / f"page_{page_index}_stage_{i + 1}.png"
                image = plot(image.copy(), layouts)
                image.save(plot_file_path, format="PNG")
                check_aborted(aborted)

        return Page(
            index=page_index,
            image=raw_image,
            body_layouts=body_layouts,
            footnotes_layouts=footnotes_layouts,
            input_tokens=context.input_tokens,
            output_tokens=context.output_tokens,
        )

    def _normalize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        text = re.sub(r"\s+", " ", text)
        return text.strip()
