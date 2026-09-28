import sys
import time
import json
from collections.abc import AsyncGenerator
from tempfile import TemporaryDirectory
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Container, Generator, Never, TypeVar, cast

from PIL.Image import Image

from ..common import AssetHub, save_xml
from ..concurrency import AsyncExecutor, NonContinuableError, OperationError
from ..error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker, OCRError, PDFError
from ..metering import AbortedCheck, check_aborted
from ..ocr_config import (
    DeepSeekOCR2VendorConfig, DeepSeekOCRVendorConfig, OCRConfig,
    UnlimitedOCRVendorConfig, VendorOCRConfig,
)
from ..runtime import IO_DOMAIN, OCR_DOMAIN, run_cancellable
from .handler import DefaultPDFHandler, PDFHandler
from .page_extractor import Page, PageExtractorNode, PageLayout
from .page_ref import PageRefContext
from .types import DeepSeekOCRSize, PDFDocumentMetadata, encode
from .vendor_ocr import VendorOCRInput, VendorOCRRuntime, VendorOCRResponse


_T = TypeVar("_T", bound=Exception)


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


@dataclass(frozen=True)
class _PreparedVendorPage:
    page_index: int
    image_path: Path
    request_path: Path
    started: float
    total_pages: int
    scale_x: float = 1.0
    scale_y: float = 1.0


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
        del ocr_size
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
                        request_path = image_path
                        scale_x = 1.0
                        scale_y = 1.0
                        if isinstance(self._config, UnlimitedOCRVendorConfig):
                            width, height = image.size
                            maximum = max(width, height)
                            if maximum > 8192:
                                ratio = 8192 / maximum
                                resized_width = max(1, round(width * ratio))
                                resized_height = max(1, round(height * ratio))
                                resized = image.resize((resized_width, resized_height))
                                request_path = (
                                    Path(temporary.name)
                                    / f"page_{ref.page_index}_request.png"
                                )
                                resized.save(request_path, format="PNG")
                                resized.close()
                                scale_x = width / resized_width
                                scale_y = height / resized_height
                        image.close()
                        events.append(OCREvent(
                            OCREventKind.RENDERED, ref.page_index, total_pages,
                            int((time.perf_counter() - started) * 1000),
                        ))
                        prepared.append(_PreparedVendorPage(
                            page_index=ref.page_index,
                            image_path=image_path,
                            request_path=request_path,
                            started=started,
                            total_pages=total_pages,
                            scale_x=scale_x,
                            scale_y=scale_y,
                        ))
                return prepared

            prepared = await run_cancellable(
                IO_DOMAIN,
                render_pages,
                original_aborted=aborted,
            )
            for event in events:
                yield event

            async def finish_page(
                item: _PreparedVendorPage,
                page: Page | None,
                operation_error: OperationError | None,
            ) -> OCREvent:
                nonlocal usable_pages
                page_index = item.page_index
                recognized_error: Exception | None = None
                if operation_error is not None:
                    from doc_page_extractor.extraction_context import (
                        ExtractionAbortedError,
                    )
                    interrupted = _find_cause(
                        operation_error, ExtractionAbortedError,
                    )
                    if interrupted is not None:
                        raise interrupted
                    cause = operation_error.__cause__
                    recognized_error = cause if isinstance(cause, OCRError) else None
                    if not isinstance(recognized_error, OCRError):
                        recognized_error = OCRError(
                            f"Failed to extract page {page_index} layout at stage 1.",
                            page_index, 1,
                        )
                        recognized_error.__cause__ = operation_error
                    if not _check_ignore_error(ignore_ocr_errors, recognized_error):
                        raise recognized_error

                    def fallback():
                        from PIL import Image as PILImage
                        with PILImage.open(item.image_path) as opened:
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
                    page_index, item.total_pages,
                    int((time.perf_counter() - item.started) * 1000),
                    committed_page.input_tokens,
                    committed_page.output_tokens,
                    recognized_error,
                )

            async with VendorOCRRuntime(
                cast(VendorOCRConfig, self._config), executor,
            ) as runtime:
                if max_tokens is not None or max_output_tokens is not None:
                    # Exact cumulative budgets settle a complete page before
                    # admitting the next vendor operation.
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
                            page = await self._recognize_vendor_page(
                                runtime,
                                item,
                                asset_hub=asset_hub,
                                includes_footnotes=includes_footnotes,
                                plot_path=plot_path,
                                aborted=aborted,
                            )
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
                    async for item, page, error in self._recognize_vendor_pages(
                        runtime,
                        prepared,
                        asset_hub=asset_hub,
                        includes_footnotes=includes_footnotes,
                        plot_path=plot_path,
                        aborted=aborted,
                    ):
                        yield await finish_page(item, page, error)
            await IO_DOMAIN.run(self._save_page_pixel_sizes, geometry_path)
            if terminal_failures and usable_pages == 0:
                from ..error import NoUsableOCRPagesError
                raise NoUsableOCRPagesError(tuple(sorted(terminal_failures)))
            if not did_ignore_any and not terminal_failures:
                await IO_DOMAIN.run(done_path.touch)
        finally:
            await IO_DOMAIN.run(temporary.cleanup)

    async def _recognize_vendor_page(
        self,
        runtime: VendorOCRRuntime,
        item: _PreparedVendorPage,
        *,
        asset_hub: AssetHub,
        includes_footnotes: bool,
        plot_path: Path | None,
        aborted: AbortedCheck,
    ) -> Page:
        try:
            try:
                first_response = await runtime.request(VendorOCRInput(
                    item.page_index, item.request_path, aborted, 1,
                ))
            except NonContinuableError:
                raise
            except Exception as error:
                _raise_vendor_page_error(item.page_index, 1, error)
            first_result = await OCR_DOMAIN.run(
                _parse_vendor_response, self._config, item, first_response,
            )
            results = [(item.image_path, first_result)]
            input_tokens = first_response.input_tokens
            output_tokens = first_response.output_tokens
            if includes_footnotes and _supports_vendor_stages(self._config):
                try:
                    second_path = await OCR_DOMAIN.run(
                        _prepare_second_stage,
                        item,
                        first_result,
                        Path(item.image_path.parent),
                        aborted,
                    )
                    second_response = await runtime.request(VendorOCRInput(
                        item.page_index, second_path, aborted, 2,
                    ))
                    second_result = await OCR_DOMAIN.run(
                        _parse_vendor_response,
                        self._config,
                        _PreparedVendorPage(
                            item.page_index,
                            item.image_path,
                            second_path,
                            item.started,
                            item.total_pages,
                        ),
                        second_response,
                    )
                except NonContinuableError:
                    raise
                except Exception as error:
                    _raise_vendor_page_error(item.page_index, 2, error)
                results.append((second_path, second_result))
                input_tokens += second_response.input_tokens
                output_tokens += second_response.output_tokens
            return await OCR_DOMAIN.run(
                self._finish_vendor_results,
                item,
                results,
                asset_hub,
                includes_footnotes,
                plot_path,
                input_tokens,
                output_tokens,
                aborted,
            )
        except OperationError:
            raise
        except Exception as error:
            _raise_vendor_page_error(item.page_index, 1, error)

    async def _recognize_vendor_pages(
        self,
        runtime: VendorOCRRuntime,
        prepared: list[_PreparedVendorPage],
        *,
        asset_hub: AssetHub,
        includes_footnotes: bool,
        plot_path: Path | None,
        aborted: AbortedCheck,
    ):
        by_page = {item.page_index: item for item in prepared}
        second_stage: dict[
            int, tuple[_PreparedVendorPage, object, VendorOCRResponse, Path]
        ] = {}

        first_requests = (
            VendorOCRInput(item.page_index, item.request_path, aborted, 1)
            for item in prepared
        )
        async for result in runtime.request_many(first_requests):
            item = by_page[result.request.page_index]
            if result.error is not None:
                yield item, None, _vendor_page_error(
                    item.page_index, result.request.stage_index, result.error,
                )
                continue
            assert result.response is not None
            try:
                first_result = await OCR_DOMAIN.run(
                    _parse_vendor_response, self._config, item, result.response,
                )
            except Exception as error:
                yield item, None, _vendor_page_error(item.page_index, 1, error)
                continue
            if includes_footnotes and _supports_vendor_stages(self._config):
                try:
                    second_path = await OCR_DOMAIN.run(
                        _prepare_second_stage,
                        item,
                        first_result,
                        Path(item.image_path.parent),
                        aborted,
                    )
                    second_stage[item.page_index] = (
                        item, first_result, result.response, second_path,
                    )
                except Exception as error:
                    yield item, None, _vendor_page_error(
                        item.page_index, 2, error,
                    )
                continue
            try:
                page = await OCR_DOMAIN.run(
                    self._finish_vendor_results,
                    item,
                    [(item.image_path, first_result)],
                    asset_hub,
                    includes_footnotes,
                    plot_path,
                    result.response.input_tokens,
                    result.response.output_tokens,
                    aborted,
                )
                yield item, page, None
            except Exception as error:
                yield item, None, _vendor_page_error(item.page_index, 1, error)

        if not second_stage:
            return

        second_requests = (
            VendorOCRInput(page_index, values[3], aborted, 2)
            for page_index, values in second_stage.items()
        )
        async for result in runtime.request_many(second_requests):
            item, first_result, first_response, second_path = second_stage[
                result.request.page_index
            ]
            if result.error is not None:
                yield item, None, _vendor_page_error(
                    item.page_index, result.request.stage_index, result.error,
                )
                continue
            assert result.response is not None
            try:
                second_result = await OCR_DOMAIN.run(
                    _parse_vendor_response,
                    self._config,
                    _PreparedVendorPage(
                        item.page_index,
                        item.image_path,
                        second_path,
                        item.started,
                        item.total_pages,
                    ),
                    result.response,
                )
                page = await OCR_DOMAIN.run(
                    self._finish_vendor_results,
                    item,
                    [
                        (item.image_path, first_result),
                        (second_path, second_result),
                    ],
                    asset_hub,
                    includes_footnotes,
                    plot_path,
                    first_response.input_tokens + result.response.input_tokens,
                    first_response.output_tokens + result.response.output_tokens,
                    aborted,
                )
                yield item, page, None
            except Exception as error:
                yield item, None, _vendor_page_error(item.page_index, 2, error)

    def _finish_vendor_results(
        self,
        item: _PreparedVendorPage,
        results,
        asset_hub: AssetHub,
        includes_footnotes: bool,
        plot_path: Path | None,
        input_tokens: int,
        output_tokens: int,
        aborted: AbortedCheck,
    ) -> Page:
        from PIL import Image as PILImage

        loaded = []
        raw_image = None
        try:
            for path, page_result in results:
                with PILImage.open(path) as opened:
                    image = opened.copy()
                loaded.append((image, page_result))
            if item.page_index == 1 and loaded:
                raw_image = loaded[0][0].copy()
            return self._extractor.results2page(
                results=loaded,
                page_index=item.page_index,
                asset_hub=asset_hub,
                includes_footnotes=includes_footnotes,
                raw_image=raw_image,
                plot_path=plot_path,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                aborted=aborted,
            )
        finally:
            for image, _ in loaded:
                image.close()

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


def _supports_vendor_stages(config: OCRConfig) -> bool:
    return isinstance(config, (
        DeepSeekOCRVendorConfig,
        DeepSeekOCR2VendorConfig,
    ))


def _parse_vendor_response(
    config: OCRConfig,
    item: _PreparedVendorPage,
    response: VendorOCRResponse,
):
    from PIL import Image as PILImage
    from doc_page_extractor.structure import build_structured_page
    from doc_page_extractor.types import OCRPageResult

    if isinstance(config, DeepSeekOCRVendorConfig):
        from doc_page_extractor.adapters.deepseek import parse_deepseek_ocr_layouts
        with PILImage.open(item.request_path) as image:
            layouts = parse_deepseek_ocr_layouts(
                cast(Any, image), response.raw_text or "", source="deepseek-ocr-vendor",
            )
        source = "deepseek-ocr-vendor"
    elif isinstance(config, DeepSeekOCR2VendorConfig):
        from doc_page_extractor.adapters.deepseek import parse_deepseek_ocr2_layouts
        with PILImage.open(item.request_path) as image:
            layouts = parse_deepseek_ocr2_layouts(
                cast(Any, image), response.raw_text or "", source="deepseek-ocr2-vendor",
            )
        source = "deepseek-ocr2-vendor"
    elif isinstance(config, UnlimitedOCRVendorConfig):
        from doc_page_extractor.adapters.unlimited import parse_unlimited_ocr_layouts
        layouts = parse_unlimited_ocr_layouts(
            response.data["parse_result"], source="unlimited-ocr-vendor",
        )
        if item.scale_x != 1.0 or item.scale_y != 1.0:
            for layout in layouts:
                x1, y1, x2, y2 = layout.det
                layout.det = (
                    round(x1 * item.scale_x),
                    round(y1 * item.scale_y),
                    round(x2 * item.scale_x),
                    round(y2 * item.scale_y),
                )
                if layout.polygon is not None:
                    layout.polygon = [
                        (
                            round(x * item.scale_x),
                            round(y * item.scale_y),
                        )
                        for x, y in layout.polygon
                    ]
        source = "unlimited-ocr-vendor"
    else:
        raise TypeError(f"Unsupported vendor OCR config: {type(config).__name__}")
    return OCRPageResult(
        layouts=layouts,
        source=source,
        structured=build_structured_page(layouts),
        raw_text=response.raw_text,
        raw=response.data,
    )


def _prepare_second_stage(
    item: _PreparedVendorPage,
    first_result,
    directory: Path,
    aborted: AbortedCheck,
) -> Path:
    from PIL import Image as PILImage
    from doc_page_extractor.redacter import background_color, redact

    check_aborted(aborted)
    with PILImage.open(item.image_path) as opened:
        image = opened.copy()
    try:
        redacted = redact(
            image=image,
            fill_color=background_color(image),
            rectangles=_redact_rectangles(image.size, (
                layout.det for layout in first_result.layouts
            )),
        )
        path = directory / f"page_{item.page_index}_stage_2.png"
        redacted.save(path, format="PNG")
        if redacted is not image:
            redacted.close()
        check_aborted(aborted)
        return path
    finally:
        image.close()


def _redact_rectangles(
    size: tuple[int, int],
    dets,
):
    width, height = size
    y_cutted = round(height * (2 / 3))
    yield (0, 0, width, y_cutted)
    parts: list[tuple[int, int, int]] = []
    for x1, _, x2, y2 in dets:
        part_height = y2 - y_cutted
        if part_height > 0:
            parts.append((x1, x2, part_height))
    parts.sort()
    forbidden = -sys.maxsize
    for index, (x1, x2, part_height) in enumerate(parts):
        left = max(x1, forbidden)
        right = x2
        for next_x1, _, next_height in parts[index + 1:]:
            if next_height > part_height:
                right = min(right, next_x1)
        if left < right:
            yield (left, y_cutted, right, y_cutted + part_height)
            forbidden = right


def _vendor_page_error(
    page_index: int,
    step_index: int,
    error: Exception,
) -> OperationError:
    ocr_error = OCRError(
        f"Failed to extract page {page_index} layout at stage {step_index}.",
        page_index=page_index,
        step_index=step_index,
    )
    ocr_error.__cause__ = error
    return OperationError(str(ocr_error), cause=ocr_error)


def _raise_vendor_page_error(
    page_index: int,
    step_index: int,
    error: Exception,
) -> Never:
    operation_error = _vendor_page_error(page_index, step_index, error)
    raise operation_error from operation_error.__cause__


def _find_cause(
    error: BaseException,
    error_type: type[_T],
) -> _T | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, error_type):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None

def _check_ignore_error(check: bool | Callable[[_T], bool], error: _T) -> bool:
    if isinstance(check, bool):
        return check
    else:
        return check(error)
