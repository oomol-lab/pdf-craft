from dataclasses import dataclass
import sys
import time

from typing import Container, Generator
from enum import auto, Enum
from pathlib import Path
from os import PathLike

from ..common import save_xml, AssetHub
from ..errors import check_aborted, AbortedCheck
from .page_extractor import PageExtractorNode
from .page_ref import PageRefContext
from .types import encode, DeepSeekOCRModel


class OCREventKind(Enum):
    START = auto()
    IGNORE = auto()
    SKIP = auto()
    COMPLETE = auto()

@dataclass
class OCREvent:
    kind: OCREventKind
    page_index: int
    total_pages: int
    cost_time_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

class OCR:
    def __init__(
            self,
            model_path: PathLike | None,
            local_only: bool,
        ) -> None:
        self._extractor = PageExtractorNode(
            model_path=model_path,
            local_only=local_only,
        )

    def predownload(self) -> None:
        self._extractor.download_models()

    def load_models(self) -> None:
        self._extractor.load_models()

    def recognize(
            self,
            pdf_path: Path,
            asset_path: Path,
            ocr_path: Path,
            model: DeepSeekOCRModel = "gundam",
            includes_footnotes: bool = False,
            plot_path: Path | None = None,
            cover_path: Path | None = None,
            aborted: AbortedCheck = lambda: False,
            page_indexes: Container[int] = range(1, sys.maxsize),
            max_tokens: int | None = None,
            max_output_tokens: int | None = None,
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

                    page = ref.extract(
                        model=model,
                        includes_footnotes=includes_footnotes,
                        includes_raw_image=(ref.page_index == 1),
                        plot_path=plot_path,
                        max_tokens=remain_tokens,
                        max_output_tokens=remain_output_tokens,
                    )
                    save_xml(encode(page), file_path)

                    if cover_path and page.image:
                        cover_path.parent.mkdir(parents=True, exist_ok=True)
                        page.image.save(cover_path, format="PNG")

                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

                    yield OCREvent(
                        kind=OCREventKind.COMPLETE,
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