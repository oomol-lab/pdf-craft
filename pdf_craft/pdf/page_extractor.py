import re
import tempfile
from pathlib import Path

from PIL.Image import Image

from ..common import ASSET_TAGS, AssetHub, remove_surrogates
from ..error import OCRError
from ..metering import AbortedCheck, check_aborted
from ..ocr_config import (
    DeepSeekOCR2LocalConfig,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRLocalConfig,
    DeepSeekOCRVendorConfig,
    OCRConfig,
    UnlimitedOCRLocalConfig,
    UnlimitedOCRVendorConfig,
)
from .ngrams import has_repetitive_ngrams
from .types import DeepSeekOCRSize, Page, PageLayout

_LAYOUT_KIND_TO_REF = {
    "text": "text",
    "title": "sub_title",
    "image": "image",
    "image_caption": "image_caption",
    "table": "table",
    "table_caption": "table_caption",
    "equation": "equation",
    "equation_caption": "equation_caption",
    "footnote": "text",
    "aside": "text",
}

_LOCAL_OCR_CONFIG_TYPES = (
    DeepSeekOCRLocalConfig,
    DeepSeekOCR2LocalConfig,
    UnlimitedOCRLocalConfig,
)


def _create_local_page_extractor(factory):
    """Turn a missing optional local runtime into an actionable package error."""
    try:
        return factory()
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Local OCR requires the optional local runtime. "
            "Install it with: pip install 'pdf-craft[local]'"
        ) from error


class PageExtractorNode:
    def __init__(self, ocr: OCRConfig) -> None:
        self._ocr = ocr
        self._page_extractor = None

    def _get_page_extractor(self):
        if not self._page_extractor:
            self._page_extractor = self._create_page_extractor()
        return self._page_extractor

    def _create_page_extractor(self):
        # 尽可能推迟 doc-page-extractor 的加载时间
        if isinstance(self._ocr, DeepSeekOCRLocalConfig):
            ocr = self._ocr
            from doc_page_extractor.extractor import create_deepseek_ocr_page_extractor

            return _create_local_page_extractor(
                lambda: create_deepseek_ocr_page_extractor(
                    ocr_model="deepseek-ocr",
                    model_path=ocr.models_cache_path,
                    local_only=ocr.local_only,
                    enable_devices_numbers=ocr.enable_devices_numbers,
                )
            )
        if isinstance(self._ocr, DeepSeekOCR2LocalConfig):
            ocr = self._ocr
            from doc_page_extractor.extractor import create_deepseek_ocr_page_extractor

            return _create_local_page_extractor(
                lambda: create_deepseek_ocr_page_extractor(
                    ocr_model="deepseek-ocr2",
                    model_path=ocr.models_cache_path,
                    local_only=ocr.local_only,
                    enable_devices_numbers=ocr.enable_devices_numbers,
                )
            )
        if isinstance(self._ocr, UnlimitedOCRLocalConfig):
            ocr = self._ocr
            from doc_page_extractor.extractor import create_unlimited_ocr_page_extractor

            return _create_local_page_extractor(
                lambda: create_unlimited_ocr_page_extractor(
                    model_path=ocr.models_cache_path,
                    local_only=ocr.local_only,
                    enable_devices_numbers=ocr.enable_devices_numbers,
                )
            )
        if isinstance(self._ocr, DeepSeekOCRVendorConfig):
            from doc_page_extractor.adapters.deepseek import (
                DeepSeekOCRVendorConfig as UpstreamDeepSeekOCRVendorConfig,
            )
            from doc_page_extractor.extractor import (
                create_deepseek_ocr_vendor_page_extractor,
            )

            return create_deepseek_ocr_vendor_page_extractor(
                UpstreamDeepSeekOCRVendorConfig(
                    base_url=self._ocr.base_url,
                    api_key=self._ocr.api_key,
                    model=self._ocr.model,
                    temperature=self._ocr.temperature,
                    top_p=self._ocr.top_p,
                    max_tokens=self._ocr.max_tokens,
                    timeout_seconds=self._ocr.timeout_seconds,
                )
            )
        if isinstance(self._ocr, DeepSeekOCR2VendorConfig):
            from doc_page_extractor.adapters.deepseek import (
                DeepSeekOCR2VendorConfig as UpstreamDeepSeekOCR2VendorConfig,
            )
            from doc_page_extractor.extractor import (
                create_deepseek_ocr2_vendor_page_extractor,
            )

            return create_deepseek_ocr2_vendor_page_extractor(
                UpstreamDeepSeekOCR2VendorConfig(
                    base_url=self._ocr.base_url,
                    api_key=self._ocr.api_key,
                    model=self._ocr.model,
                    temperature=self._ocr.temperature,
                    top_p=self._ocr.top_p,
                    max_tokens=self._ocr.max_tokens,
                    timeout_seconds=self._ocr.timeout_seconds,
                )
            )
        if isinstance(self._ocr, UnlimitedOCRVendorConfig):
            from doc_page_extractor.adapters.unlimited import (
                UnlimitedOCRVendorConfig as UpstreamUnlimitedOCRVendorConfig,
            )
            from doc_page_extractor.extractor import (
                create_unlimited_ocr_vendor_page_extractor,
            )

            return create_unlimited_ocr_vendor_page_extractor(
                UpstreamUnlimitedOCRVendorConfig(
                    ak=self._ocr.ak,
                    sk=self._ocr.sk,
                    base_url=self._ocr.base_url,
                    poll_interval_seconds=self._ocr.poll_interval_seconds,
                    timeout_seconds=self._ocr.timeout_seconds,
                )
            )
        raise TypeError(f"Unsupported OCR config: {type(self._ocr).__name__}")

    def download_models(self, revision: str | None) -> None:
        if not isinstance(self._ocr, _LOCAL_OCR_CONFIG_TYPES):
            raise RuntimeError("download_models is only available for local OCR.")
        self._get_page_extractor().download_ocr_model(revision)

    def load_models(self) -> None:
        if not isinstance(self._ocr, _LOCAL_OCR_CONFIG_TYPES):
            raise RuntimeError("load_models is only available for local OCR.")
        self._get_page_extractor().load_ocr_model()

    def image2page(
        self,
        image: Image,
        page_index: int,
        asset_hub: AssetHub,
        ocr_size: DeepSeekOCRSize,
        includes_footnotes: bool,
        includes_raw_image: bool,
        plot_path: Path | None,
        max_tokens: int | None,
        max_output_tokens: int | None,
        device_number: int | None,
        aborted: AbortedCheck,
    ) -> Page:
        self._validate_ocr_size(ocr_size)
        from doc_page_extractor.extraction_context import AbortError, TokenLimitError
        from doc_page_extractor.plot import plot
        from doc_page_extractor.types import ExtractionContext

        body_layouts: list[PageLayout] = []
        footnotes_layouts: list[PageLayout] = []
        raw_image: Image | None = None

        if includes_raw_image:
            raw_image = image
            image = image.copy()

        with tempfile.TemporaryDirectory() as temp_dir_path:
            context = ExtractionContext(
                check_aborted=aborted,
                max_tokens=max_tokens,
                max_output_tokens=max_output_tokens,
                output_dir_path=temp_dir_path,
            )
            step_index: int = 1
            generator = self._get_page_extractor().extract_page_results(
                image=image,
                size=ocr_size,
                stages=2 if includes_footnotes else 1,
                context=context,
                device_number=device_number,
            )
            while True:
                try:
                    image, page_result = next(generator)
                except StopIteration:
                    break
                except AbortError:
                    raise
                except TokenLimitError:
                    raise
                except Exception as error:
                    raise OCRError(
                        f"Failed to extract page {page_index} layout at stage {step_index}.",
                        page_index=page_index,
                        step_index=step_index,
                    ) from error

                for page_layout, is_footnote in self._iter_page_layouts(
                    image=image,
                    structured=page_result.structured,
                    asset_hub=asset_hub,
                    stage_index=step_index,
                    includes_footnotes=includes_footnotes,
                ):
                    if is_footnote:
                        page_layout.order = len(footnotes_layouts)
                        footnotes_layouts.append(page_layout)
                    elif step_index == 1:
                        page_layout.order = len(body_layouts)
                        body_layouts.append(page_layout)
                    elif page_layout.ref not in ASSET_TAGS:
                        page_layout.order = len(footnotes_layouts)
                        footnotes_layouts.append(page_layout)

                check_aborted(aborted)
                if plot_path is not None:
                    plot_file_path = (
                        plot_path / f"page_{page_index}_stage_{step_index}.png"
                    )
                    image = plot(image.copy(), page_result.layouts)
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

    def _iter_page_layouts(
        self,
        image: Image,
        structured,
        asset_hub: AssetHub,
        stage_index: int,
        includes_footnotes: bool,
    ):
        if structured is None:
            return

        for block in structured.blocks:
            kind = str(block.kind.value)
            ref = _LAYOUT_KIND_TO_REF.get(kind, "unknown")
            if ref == "unknown":
                continue
            is_footnote = kind == "footnote"
            if is_footnote and not includes_footnotes:
                continue

            text = self._normalize_block_text(block)
            det = self._normalize_layout_det(image.size, block.det)
            if det is None:
                continue

            # 检测短模式重复（如 "1.1.1.1."）
            if has_repetitive_ngrams(
                text, min_ngram=2, max_ngram=5, repeat_threshold=16
            ):
                continue

            # 检测长模式重复（保守策略）
            if has_repetitive_ngrams(
                text, min_ngram=6, max_ngram=20, repeat_threshold=8
            ):
                continue

            if stage_index != 1 and ref in ASSET_TAGS:
                continue

            asset_hash: str | None = None
            if ref in ASSET_TAGS:
                asset_hash = asset_hub.clip(image, det)

            yield PageLayout(
                ref=ref,
                det=det,
                text=text,
                hash=asset_hash,
                order=0,
            ), is_footnote

    def _normalize_block_text(self, block) -> str:
        parts: list[str] = []
        text = block.html if block.html is not None else block.text
        text = self._normalize_text(text)
        if text:
            parts.append(text)

        for child in block.children:
            child_text = child.html if child.html is not None else child.text
            child_text = self._normalize_text(child_text)
            if child_text:
                parts.append(child_text)

        return "\n".join(parts)

    def _normalize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        text = remove_surrogates(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _validate_ocr_size(self, ocr_size: DeepSeekOCRSize) -> None:
        if isinstance(self._ocr, DeepSeekOCR2LocalConfig) and ocr_size == "tiny":
            raise ValueError(
                "deepseek-ocr2-local is not reliable with ocr_size='tiny'; "
                "use ocr_size='base' for the validated local OCR2 path."
            )

    def _normalize_layout_det(
        self,
        size: tuple[int, int],
        det: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int] | None:
        width, height = size
        left, top, right, bottom = det
        left = max(0, min(left, width))
        top = max(0, min(top, height))
        right = max(0, min(right, width))
        bottom = max(0, min(bottom, height))

        if left >= right or top >= bottom:
            return None
        return left, top, right, bottom
