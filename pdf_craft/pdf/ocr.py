import sys
import time
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from tempfile import TemporaryDirectory
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import Callable, Container, Generator, TypeVar

from PIL.Image import Image

from ..common import AssetHub, save_xml
from ..concurrency import AsyncExecutor, NonContinuableError, OperationError
from ..error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker, OCRError, PDFError
from ..metering import AbortedCheck, check_aborted
from ..ocr_config import (
    DeepSeekOCR2VendorConfig, DeepSeekOCRVendorConfig, OCRConfig,
    UnlimitedOCRVendorConfig,
)
from ..runtime import IO_DOMAIN, run_cancellable
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
        ocr: OCRConfig,
        pdf_handler: PDFHandler | None,
    ) -> None:
        self._pdf_handler = pdf_handler
        self._config = ocr
        self._pdf_handler_lock = Lock()
        self._extractor = PageExtractorNode(ocr=ocr)
        self._last_page_pixel_sizes: dict[int, tuple[int, int]] = {}

    @property
    def is_vendor(self) -> bool:
        return isinstance(self._config, (
            DeepSeekOCRVendorConfig,
            DeepSeekOCR2VendorConfig,
            UnlimitedOCRVendorConfig,
        ))

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

    async def recognize_vendor(
        self,
        executor: AsyncExecutor,
        *,
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
    ) -> AsyncGenerator[OCREvent, None]:
        """Keep PDF rendering serial while vendor page calls share async capacity."""
        if not self.is_vendor:
            raise RuntimeError("recognize_vendor is only available for vendor OCR")
        ocr_path.mkdir(parents=True, exist_ok=True)
        if plot_path is not None:
            plot_path.mkdir(parents=True, exist_ok=True)
        geometry_path = ocr_path / "page_pixel_sizes.json"
        self._last_page_pixel_sizes = self._load_page_pixel_sizes(geometry_path)
        done_path = ocr_path / "done"
        if done_path.exists() and not any(ocr_path.glob("page_*.failed")):
            return

        temporary = await IO_DOMAIN.run(
            TemporaryDirectory, prefix="pdf-craft-vendor-ocr-",
        )
        events: list[OCREvent] = []
        asset_hub = AssetHub(asset_path)
        terminal_failures: list[int] = []
        usable_pages = 0
        did_ignore_any = False
        try:
            def render_pages(cooperative_aborted: AbortedCheck):
                nonlocal usable_pages, did_ignore_any
                prepared = []
                with PageRefContext(
                    pdf_path=pdf_path,
                    pdf_handler=self._get_pdf_handler(),
                ) as refs:
                    total_pages = refs.pages_count
                    for ref in refs:
                        check_aborted(cooperative_aborted)
                        started = time.perf_counter()
                        events.append(OCREvent(
                            OCREventKind.START, ref.page_index, total_pages,
                        ))
                        if ref.page_index not in page_indexes:
                            did_ignore_any = True
                            events.append(OCREvent(
                                OCREventKind.IGNORE, ref.page_index, total_pages,
                                int((time.perf_counter() - started) * 1000),
                            ))
                            continue
                        target = ocr_path / f"page_{ref.page_index}.xml"
                        failed = ocr_path / f"page_{ref.page_index}.failed"
                        if target.exists() and not failed.exists():
                            usable_pages += 1
                            events.append(OCREvent(
                                OCREventKind.SKIP, ref.page_index, total_pages,
                                int((time.perf_counter() - started) * 1000),
                            ))
                            continue
                        try:
                            image = ref.render(
                                dpi=dpi if dpi is not None else 300,
                                max_image_file_size=max_page_image_file_size,
                            )
                        except PDFError as error:
                            if not _check_ignore_error(ignore_pdf_errors, error):
                                raise
                            page = self._create_fallback_page(
                                asset_hub, ref.page_index, None,
                            )
                            self._last_page_pixel_sizes[ref.page_index] = (100, 100)
                            self._save_page_pixel_sizes(geometry_path)
                            save_xml(encode(page), target)
                            failed.write_text(type(error).__name__, encoding="utf-8")
                            terminal_failures.append(ref.page_index)
                            events.append(OCREvent(
                                OCREventKind.FAILED, ref.page_index, total_pages,
                                int((time.perf_counter() - started) * 1000),
                                error=error,
                            ))
                            continue
                        self._last_page_pixel_sizes[ref.page_index] = image.size
                        image_path = Path(temporary.name) / f"page_{ref.page_index}.png"
                        image.save(image_path, format="PNG")
                        image.close()
                        events.append(OCREvent(
                            OCREventKind.RENDERED, ref.page_index, total_pages,
                            int((time.perf_counter() - started) * 1000),
                        ))
                        prepared.append((ref.page_index, image_path, started, total_pages))
                return prepared

            prepared = await run_cancellable(
                IO_DOMAIN,
                render_pages,
                original_aborted=aborted,
            )
            for event in events:
                yield event

            async def recognize_page(
                item: tuple[int, Path, float, int],
                remaining_tokens: int | None,
                remaining_output_tokens: int | None,
            ) -> Page:
                page_index, image_path, _, _ = item

                def execute(cooperative_aborted: AbortedCheck):
                    from PIL import Image as PILImage
                    with PILImage.open(image_path) as opened:
                        image = opened.copy()
                    return self._extractor.image2page(
                        image=image,
                        page_index=page_index,
                        asset_hub=asset_hub,
                        ocr_size=ocr_size,
                        includes_footnotes=includes_footnotes,
                        includes_raw_image=(page_index == 1),
                        plot_path=plot_path,
                        max_tokens=remaining_tokens,
                        max_output_tokens=remaining_output_tokens,
                        device_number=None,
                        aborted=cooperative_aborted,
                    )

                return await run_cancellable(
                    IO_DOMAIN,
                    execute,
                    original_aborted=aborted,
                )

            async def finish_page(
                item: tuple[int, Path, float, int],
                page: Page | None,
                operation_error: OperationError | None,
            ) -> OCREvent:
                nonlocal usable_pages
                page_index, image_path, started, total_pages = item
                recognized_error: Exception | None = None
                if operation_error is not None:
                    cause = operation_error.__cause__
                    from doc_page_extractor.extraction_context import (
                        ExtractionAbortedError,
                    )
                    if isinstance(cause, ExtractionAbortedError):
                        raise cause
                    recognized_error = (
                        cause if isinstance(cause, Exception) else operation_error
                    )
                    if not isinstance(recognized_error, OCRError):
                        recognized_error = OCRError(
                            f"Failed to extract page {page_index} layout.",
                            page_index, 1,
                        )
                    if not _check_ignore_error(ignore_ocr_errors, recognized_error):
                        raise recognized_error

                    def fallback():
                        from PIL import Image as PILImage
                        with PILImage.open(image_path) as opened:
                            image = opened.copy()
                        return self._create_fallback_page(
                            asset_hub, page_index, image,
                        )

                    page = await IO_DOMAIN.run(fallback)
                assert page is not None
                committed_page = page

                def commit_page():
                    failed = ocr_path / f"page_{page_index}.failed"
                    # Persist geometry before publishing the page XML.  A page
                    # that is treated as a resumable cache hit must always have
                    # its source dimensions available on the next run.
                    self._save_page_pixel_sizes(geometry_path)
                    if recognized_error is None:
                        failed.unlink(missing_ok=True)
                    else:
                        failed.write_text(
                            type(recognized_error).__name__, encoding="utf-8",
                        )
                    save_xml(
                        encode(committed_page),
                        ocr_path / f"page_{page_index}.xml",
                    )
                    if cover_path and committed_page.image:
                        cover_path.parent.mkdir(parents=True, exist_ok=True)
                        committed_page.image.save(cover_path, format="PNG")

                await IO_DOMAIN.run(commit_page)
                if recognized_error is None:
                    usable_pages += 1
                else:
                    terminal_failures.append(page_index)
                return OCREvent(
                    OCREventKind.COMPLETE if recognized_error is None else OCREventKind.FAILED,
                    page_index, total_pages,
                    int((time.perf_counter() - started) * 1000),
                    committed_page.input_tokens,
                    committed_page.output_tokens,
                    recognized_error,
                )

            if max_tokens is not None or max_output_tokens is not None:
                # Cumulative limits require each completed page to settle before
                # the next request is admitted.  This keeps the public budget
                # exact instead of multiplying it by concurrent in-flight pages.
                remaining_tokens = max_tokens
                remaining_output_tokens = max_output_tokens
                from doc_page_extractor.extraction_context import TokenLimitError

                for item in prepared:
                    if remaining_tokens is not None and remaining_tokens <= 0:
                        raise TokenLimitError()
                    if (
                        remaining_output_tokens is not None
                        and remaining_output_tokens <= 0
                    ):
                        raise TokenLimitError()
                    page = None
                    operation_error = None
                    try:
                        page = await executor.run(lambda item=item: recognize_page(
                            item, remaining_tokens, remaining_output_tokens,
                        ))
                    except NonContinuableError:
                        raise
                    except OperationError as error:
                        operation_error = error
                    event = await finish_page(item, page, operation_error)
                    if remaining_tokens is not None:
                        remaining_tokens -= event.input_tokens + event.output_tokens
                    if remaining_output_tokens is not None:
                        remaining_output_tokens -= event.output_tokens
                    yield event
            else:
                def operations():
                    for item in prepared:
                        async def recognize(operation_id: int, item=item):
                            page = await recognize_page(item, None, None)
                            return operation_id, page
                        yield recognize

                async with aclosing(executor.map(operations())) as results:
                    async for result in results:
                        item = prepared[result.operation_id]
                        page = result.value if result.succeeded else None
                        event = await finish_page(item, page, result.error)
                        yield event
            await IO_DOMAIN.run(self._save_page_pixel_sizes, geometry_path)
            if terminal_failures and usable_pages == 0:
                from ..error import NoUsableOCRPagesError
                raise NoUsableOCRPagesError(tuple(sorted(terminal_failures)))
            if not did_ignore_any and not terminal_failures:
                await IO_DOMAIN.run(done_path.touch)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

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
        geometry_path = ocr_path / "page_pixel_sizes.json"
        self._last_page_pixel_sizes = self._load_page_pixel_sizes(geometry_path)
        if plot_path is not None:
            plot_path.mkdir(parents=True, exist_ok=True)

        done_path = ocr_path / "done"
        did_ignore_any: bool = False
        did_fail_any: bool = False
        if done_path.exists() and not any(ocr_path.glob("page_*.failed")):
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
                failure_path = ocr_path / f"page_{ref.page_index}.failed"

                if file_path.exists() and not failure_path.exists():
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    yield OCREvent(
                        kind=OCREventKind.SKIP,
                        page_index=ref.page_index,
                        total_pages=pages_count,
                        cost_time_ms=elapsed_ms,
                    )
                else:
                    from doc_page_extractor.extraction_context import TokenLimitError

                    if remain_tokens is not None and remain_tokens <= 0:
                        raise TokenLimitError()
                    if remain_output_tokens is not None and remain_output_tokens <= 0:
                        raise TokenLimitError()

                    page: Page | None = None
                    image: Image | None = None
                    recognized_error: Exception | None = None

                    try:
                        rendered_image = ref.render(
                            dpi=dpi
                            if dpi is not None
                            else 300,  # DPI=300 for scanned page
                            max_image_file_size=max_page_image_file_size,
                        )
                        image = rendered_image
                        self._last_page_pixel_sizes[ref.page_index] = rendered_image.size
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
                        if image is None:
                            # A PDF rendering failure has no image dimensions.  The
                            # textual fallback still needs a valid source-page entry
                            # so chapter validation and downstream renderers can keep
                            # the remaining usable pages.
                            self._last_page_pixel_sizes[ref.page_index] = (100, 100)

                    if recognized_error is not None:
                        did_fail_any = True
                        failure_path.write_text(
                            type(recognized_error).__name__, encoding="utf-8"
                        )
                    else:
                        failure_path.unlink(missing_ok=True)

                    save_xml(encode(page), file_path)
                    self._save_page_pixel_sizes(geometry_path)

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

        if not did_ignore_any and not did_fail_any:
            done_path.touch()

    @property
    def last_page_pixel_sizes(self) -> dict[int, tuple[int, int]]:
        return self._last_page_pixel_sizes.copy()

    def _get_pdf_handler(self) -> PDFHandler:
        if self._pdf_handler is not None:
            return self._pdf_handler

        with self._pdf_handler_lock:
            if self._pdf_handler is None:
                self._pdf_handler = DefaultPDFHandler()
            return self._pdf_handler

    def _load_page_pixel_sizes(self, path: Path) -> dict[int, tuple[int, int]]:
        if not path.exists():
            return {}
        try:
            raw_sizes = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw_sizes, dict):
                raise ValueError("must be a mapping")
            sizes: dict[int, tuple[int, int]] = {}
            for raw_index, raw_size in raw_sizes.items():
                index = int(raw_index)
                if (
                    not isinstance(raw_size, list | tuple)
                    or len(raw_size) != 2
                    or not all(
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and value > 0
                        for value in raw_size
                    )
                ):
                    raise ValueError("invalid page size")
                sizes[index] = (raw_size[0], raw_size[1])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid OCR page geometry cache: {path}") from error
        if any(index < 1 or width < 1 or height < 1
               for index, (width, height) in sizes.items()):
            raise ValueError(f"invalid OCR page geometry cache: {path}")
        return sizes

    def _save_page_pixel_sizes(self, path: Path) -> None:
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(
                {str(index): list(size) for index, size in self._last_page_pixel_sizes.items()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary_path.replace(path)

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
