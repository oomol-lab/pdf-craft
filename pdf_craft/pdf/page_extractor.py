import re
import tempfile

from pathlib import Path
from typing import Iterable
from PIL.Image import Image

from ..common import ASSET_TAGS, AssetHub
from ..error import OCRError
from ..metering import check_aborted, AbortedCheck
from .types import Page, PageLayout, DeepSeekOCRModel


class PageExtractorNode:
    def __init__(
        self,
        model_path: Path | None = None,
        local_only: bool = False,
        enable_devices_numbers: Iterable[int] | None = None,
    ) -> None:
        self._model_path: Path | None = model_path
        self._local_only: bool = local_only
        self._enable_devices_numbers: Iterable[int] | None = enable_devices_numbers
        self._page_extractor = None

    def _get_page_extractor(self):
        if not self._page_extractor:
            # 尽可能推迟 doc-page-extractor 的加载时间
            from doc_page_extractor import create_page_extractor
            self._page_extractor = create_page_extractor(
                model_path=self._model_path,
                local_only=self._local_only,
                enable_devices_numbers=self._enable_devices_numbers,
            )
        return self._page_extractor

    def download_models(self, revision: str | None) -> None:
        self._get_page_extractor().download_models(revision)

    def load_models(self) -> None:
        self._get_page_extractor().load_models()

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
            device_number: int | None,
            aborted: AbortedCheck,
        ) -> Page:

        from doc_page_extractor import plot, ExtractionContext
        body_layouts: list[PageLayout] = []
        footnotes_layouts: list[PageLayout] = []
        raw_image: Image | None = None

        if includes_raw_image:
            raw_image = image
            image = image.copy()

        with tempfile.TemporaryDirectory() as temp_dir_path:
            image_path = Path(temp_dir_path) / "raw_image.png"
            image.save(image_path, format="PNG")
            context = ExtractionContext(
                check_aborted=aborted,
                max_tokens=max_tokens,
                max_output_tokens=max_output_tokens,
                output_dir_path=temp_dir_path,
            )
            step_index: int = 1
            generator = self._get_page_extractor().extract(
                image_path=image_path,
                size=model_size,
                stages=2 if includes_footnotes else 1,
                context=context,
                device_number=device_number,
            )
            while True:
                try:
                    image, layouts = next(generator)()
                except StopIteration:
                    break
                except Exception as error:
                    raise OCRError(f"Failed to extract page {page_index} layout at stage {step_index}.", page_index=page_index, step_index=step_index) from error

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
                    if step_index == 1:
                        body_layouts.append(page_layout)
                    elif step_index == 2 and ref not in ASSET_TAGS:
                        footnotes_layouts.append(page_layout)

                check_aborted(aborted)
                if plot_path is not None:
                    plot_file_path = plot_path / f"page_{page_index}_stage_{step_index}.png"
                    image = plot(image.copy(), layouts)
                    image.save(plot_file_path, format="PNG")
                    check_aborted(aborted)

                step_index += 1

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
