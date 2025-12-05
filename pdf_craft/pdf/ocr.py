from dataclasses import dataclass
import sys
import time

from typing import Container, Generator
from enum import auto, Enum
from pathlib import Path
from os import PathLike

from ..common import save_xml, AssetHub
from ..to_path import to_path
from ..error import FitzError
from ..metering import check_aborted, AbortedCheck
from .page_extractor import Page, PageLayout, PageExtractorNode
from .page_ref import PageRefContext
from .types import encode, DeepSeekOCRModel


class OCREventKind(Enum):
    START = auto()
    IGNORE = auto()
    SKIP = auto()
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
            local_only: bool,
        ) -> None:
        self._extractor = PageExtractorNode(
            model_path=to_path(model_path) if model_path is not None else None,
            local_only=local_only,
        )

    def predownload(self, revision: str | None) -> None:
        self._extractor.download_models(revision)

    def load_models(self) -> None:
        self._extractor.load_models()

    def recognize(
            self,
            pdf_path: Path,
            asset_path: Path,
            ocr_path: Path,
            model: DeepSeekOCRModel = "gundam",
            includes_footnotes: bool = False,
            ignore_fitz_errors: bool = False,
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
            extractor=self._extractor,
            asset_hub=AssetHub(asset_path),
            aborted=aborted,
        ) as refs:
            pages_count = refs.pages_count
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

                    page: Page
                    fitz_error: FitzError | None = None

                    try:
                        page = ref.extract(
                            model=model,
                            includes_footnotes=includes_footnotes,
                            includes_raw_image=(ref.page_index == 1),
                            plot_path=plot_path,
                            max_tokens=remain_tokens,
                            max_output_tokens=remain_output_tokens,
                            device_number=device_number,
                        )
                    except FitzError as error:
                        if not ignore_fitz_errors:
                            raise
                        fitz_error = error
                        page = self._create_warn_page(
                            page_index=ref.page_index,
                            text=f"[[Page {ref.page_index} extraction failed due to PDF rendering error]]",
                        )

                    save_xml(encode(page), file_path)

                    if cover_path and page.image:
                        cover_path.parent.mkdir(parents=True, exist_ok=True)
                        page.image.save(cover_path, format="PNG")

                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

                    yield OCREvent(
                        kind=OCREventKind.COMPLETE if fitz_error is None else OCREventKind.FAILED,
                        error=fitz_error,
                        page_index=ref.page_index,
                        total_pages=pages_count,
                        cost_time_ms=elapsed_ms,
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

    def _create_warn_page(self, page_index: int, text: str) -> Page:
        page = Page(
            index=page_index,
            image=None,
            body_layouts=[],
            footnotes_layouts=[],
            input_tokens=0,
            output_tokens=0,
        )
        page.body_layouts.append(PageLayout(
            ref="text",
            det=(0, 0, 100, 100),
            text=text,
            hash=None,
        ))
        return page