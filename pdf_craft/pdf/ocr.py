import sys
import time
from dataclasses import dataclass
from enum import Enum, auto
from os import PathLike
from pathlib import Path
from threading import Lock
from typing import Callable, Container, Generator, TypeVar

from PIL.Image import Image

from ..common import AssetHub, save_xml
from ..error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker, OCRError, PDFError
from ..metering import AbortedCheck, check_aborted
from ..to_path import to_path
from .handler import DefaultPDFHandler, PDFHandler
from .page_extractor import Page, PageExtractorNode, PageLayout
from .page_ref import PageRefContext
from .types import DeepSeekOCRSize, PDFDocumentMetadata, encode


class OCREventKind(Enum):
    START = auto()
    IGNORE = auto()
    SKIP = auto()
    RENDERED = auto()
    COMPLETE = auto()
    FAILED = auto()


@dataclass
class OCREvent:
    kind: OCREventKind
    page_index: int
    total_pages: int
    cost_time_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: Exception | None = None


class OCR:
    def __init__(
        self,
        model_path: PathLike | str | None,
        pdf_handler: PDFHandler | None,
        local_only: bool,
    ) -> None:
        self._pdf_handler = pdf_handler
        self._pdf_handler_lock = Lock()
        self._extractor = PageExtractorNode(
            model_path=to_path(model_path) if model_path is not None else None,
            local_only=local_only,
        )

    def predownload(self, revision: str | None) -> None:
        self._extractor.download_models(revision)

    def load_models(self) -> None:
        self._extractor.load_models()

    def metadata(self, pdf_path: Path) -> PDFDocumentMetadata:
        document = self._get_pdf_handler().open(pdf_path)
        try:
            return document.metadata()
        finally:
            document.close()

    def recognize(
        self,
        pdf_path: Path,
        asset_path: Path,
        ocr_path: Path,
        ocr_size: DeepSeekOCRSize = "gundam",
        dpi: int | None = None,
        max_page_image_file_size: int | None = None,
        includes_footnotes: bool = False,
        ignore_pdf_errors: IgnorePDFErrorsChecker = False,
        ignore_ocr_errors: IgnoreOCRErrorsChecker = False,
        plot_path: Path | None = None,
        cover_path: Path | None = None,
        aborted: AbortedCheck = lambda: False,
        page_indexes: Container[int] = range(1, sys.maxsize),
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        device_number: int | None = None,
    ) -> Generator[OCREvent, None, None]:
        ocr_path.mkdir(parents=True, exist_ok=True)
        if plot_path is not None:
            plot_path.mkdir(parents=True, exist_ok=True)

        done_path = ocr_path / "done"
        did_ignore_any: bool = False
        if done_path.exists():
            return

        remain_tokens: int | None = max_tokens
        remain_output_tokens: int | None = max_output_tokens

        with PageRefContext(
            pdf_path=pdf_path,
            pdf_handler=self._get_pdf_handler(),
        ) as refs:
            pages_count = refs.pages_count
            asset_hub = AssetHub(asset_path)

            for ref in refs:
                check_aborted(aborted)
                start_time = time.perf_counter()
                yield OCREvent(
                    kind=OCREventKind.START,
                    page_index=ref.page_index,
                    total_pages=pages_count,
                )
                if ref.page_index not in page_indexes:
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    did_ignore_any = True
                    yield OCREvent(
                        kind=OCREventKind.IGNORE,
                        page_index=ref.page_index,
                        total_pages=pages_count,
                        cost_time_ms=elapsed_ms,
                    )
                    continue

                filename = f"page_{ref.page_index}.xml"
                file_path = ocr_path / filename

                if file_path.exists():
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    yield OCREvent(
                        kind=OCREventKind.SKIP,
                        page_index=ref.page_index,
                        total_pages=pages_count,
                        cost_time_ms=elapsed_ms,
                    )
                else:
                    from doc_page_extractor import TokenLimitError

                    if remain_tokens is not None and remain_tokens <= 0:
                        raise TokenLimitError()
                    if remain_output_tokens is not None and remain_output_tokens <= 0:
                        raise TokenLimitError()

                    page: Page | None = None
                    image: Image | None = None
                    recognized_error: Exception | None = None

                    try:
                        image = ref.render(
                            dpi=dpi
                            if dpi is not None
                            else 300,  # DPI=300 for scanned page
                            max_image_file_size=max_page_image_file_size,
                        )
                        yield OCREvent(
                            kind=OCREventKind.RENDERED,
                            page_index=ref.page_index,
                            total_pages=pages_count,
                            cost_time_ms=int((time.perf_counter() - start_time) * 1000),
                            input_tokens=0,
                            output_tokens=0,
                        )
                        page = self._extractor.image2page(
                            image=image,
                            page_index=ref.page_index,
                            asset_hub=asset_hub,
                            ocr_size=ocr_size,
                            includes_footnotes=includes_footnotes,
                            includes_raw_image=(ref.page_index == 1),
                            plot_path=plot_path,
                            max_tokens=remain_tokens,
                            max_output_tokens=remain_output_tokens,
                            device_number=device_number,
                            aborted=aborted,
                        )
                    except PDFError as error:
                        if not _check_ignore_error(ignore_pdf_errors, error):
                            raise
                        recognized_error = error

                    except OCRError as error:
                        if not _check_ignore_error(ignore_ocr_errors, error):
                            raise
                        recognized_error = error

                    if page is None:
                        page = self._create_fallback_page(
                            asset_hub=asset_hub,
                            page_index=ref.page_index,
                            image=image,
                        )

                    save_xml(encode(page), file_path)

                    if cover_path and page.image:
                        cover_path.parent.mkdir(parents=True, exist_ok=True)
                        page.image.save(cover_path, format="PNG")

                    yield OCREvent(
                        kind=OCREventKind.COMPLETE
                        if recognized_error is None
                        else OCREventKind.FAILED,
                        error=recognized_error,
                        page_index=ref.page_index,
                        total_pages=pages_count,
                        cost_time_ms=int((time.perf_counter() - start_time) * 1000),
                        input_tokens=page.input_tokens,
                        output_tokens=page.output_tokens,
                    )
                    if remain_tokens is not None:
                        remain_tokens -= page.input_tokens
                        remain_tokens -= page.output_tokens

                    if remain_output_tokens is not None:
                        remain_output_tokens -= page.output_tokens

        if not did_ignore_any:
            done_path.touch()

    def _get_pdf_handler(self) -> PDFHandler:
        if self._pdf_handler is not None:
            return self._pdf_handler

        with self._pdf_handler_lock:
            if self._pdf_handler is None:
                self._pdf_handler = DefaultPDFHandler()
            return self._pdf_handler

    def _create_fallback_page(
        self,
        asset_hub: AssetHub,
        page_index: int,
        image: Image | None,
    ) -> Page:
        layout: PageLayout
        if image is not None:
            width, height = image.size
            full_page_det = (0, 0, width, height)
            image_hash = asset_hub.clip(image, full_page_det)
            layout = PageLayout(
                ref="image",
                det=full_page_det,
                text="",
                hash=image_hash,
                order=0,
            )
        else:
            layout = PageLayout(
                ref="text",
                det=(0, 0, 100, 100),
                text=f"[[Page {page_index} extraction failed due to PDF rendering error]]",
                hash=None,
                order=0,
            )
        return Page(
            index=page_index,
            image=image,
            body_layouts=[layout],
            footnotes_layouts=[],
            input_tokens=0,
            output_tokens=0,
        )


_T = TypeVar("_T", bound=Exception)


def _check_ignore_error(check: bool | Callable[[_T], bool], error: _T) -> bool:
    if isinstance(check, bool):
        return check
    else:
        return check(error)
