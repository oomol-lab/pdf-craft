import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast

from PIL import Image

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity, OperationResult,
)
from pdf_craft.error import OCRError, PDFError
from pdf_craft.pdf.ocr import OCR, OCREventKind
from pdf_craft.pdf.types import Page
from doc_page_extractor.extraction_context import AbortError, TokenLimitError


class _Document:
    pages_count = 3

    def __init__(self, rendered: list[int]) -> None:
        self._rendered = rendered

    def page_size(self, _page_index: int) -> tuple[float, float]:
        return (1, 1)

    def render_page(self, page_index: int, dpi: int) -> Image.Image:
        del dpi
        self._rendered.append(page_index)
        return Image.new("RGB", (12, 16), "white")

    def close(self) -> None:
        return None


class _Handler:
    def __init__(self, rendered: list[int]) -> None:
        self._rendered = rendered

    def open(self, _pdf_path: Path) -> _Document:
        return _Document(self._rendered)


class _Extractor:
    def __init__(
        self,
        rendered: list[int],
        fail_pages: tuple[int, ...] = (2,),
        errors: dict[int, Exception] | None = None,
    ) -> None:
        self._rendered = rendered
        self._fail_pages = fail_pages
        self._errors = errors or {}
        self._lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.started_after_render: list[bool] = []
        self.budgets: list[tuple[int | None, int | None]] = []

    def image2page(
        self, *, page_index: int,
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        **_kwargs,
    ) -> Page:
        with self._lock:
            self.started_after_render.append(self._rendered == [1, 2, 3])
            self.budgets.append((max_tokens, max_output_tokens))
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            time.sleep(0.02)
            if page_index in self._errors:
                raise self._errors[page_index]
            if page_index in self._fail_pages:
                raise OCRError("ignored", page_index=page_index, step_index=1)
            return Page(page_index, None, [], [], page_index, page_index)
        finally:
            with self._lock:
                self.active -= 1


class _SequentialResultIterator:
    def __init__(self, operations) -> None:
        self._operations = iter(operations)
        self._operation_id = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            operation = next(self._operations)
        except StopIteration:
            raise StopAsyncIteration from None
        operation_id = self._operation_id
        self._operation_id += 1
        returned_id, value = await operation(operation_id)
        return OperationResult(returned_id, value=value)

    async def aclose(self) -> None:
        self.closed = True
        close = getattr(self._operations, "close", None)
        if close is not None:
            close()


class _SequentialExecutor:
    def __init__(self) -> None:
        self.results: list[_SequentialResultIterator] = []

    async def run(self, operation):
        return await operation()

    def map(self, operations):
        results = _SequentialResultIterator(operations)
        self.results.append(results)
        return results


class VendorOCRConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_vendor_ocr_accepts_custom_closable_executor_iterator(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        ocr.__dict__["_extractor"] = _Extractor(rendered, fail_pages=())
        executor = _SequentialExecutor()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                event
                async for event in ocr.recognize_vendor(
                    executor,
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                )
            ]

        self.assertEqual(
            [event.page_index for event in events if event.kind == OCREventKind.COMPLETE],
            [1, 2, 3],
        )
        self.assertEqual(len(executor.results), 1)
        self.assertTrue(executor.results[0].closed)

    async def test_vendor_io_is_concurrent_after_serial_render_and_fallback_finishes(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        extractor = _Extractor(rendered)
        ocr.__dict__["_extractor"] = extractor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                event
                async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(2)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    ignore_ocr_errors=True,
                )
            ]

            self.assertEqual(rendered, [1, 2, 3])
            self.assertTrue(all(extractor.started_after_render))
            self.assertEqual(extractor.maximum, 2)
            self.assertTrue(all(
                (root / "ocr" / f"page_{index}.xml").exists()
                for index in (1, 2, 3)
            ))
            self.assertTrue((root / "ocr" / "page_2.failed").exists())
            terminal = [
                event for event in events
                if event.kind in (OCREventKind.COMPLETE, OCREventKind.FAILED)
            ]
            self.assertEqual({event.page_index for event in terminal}, {1, 2, 3})
            self.assertEqual(
                next(event.kind for event in terminal if event.page_index == 2),
                OCREventKind.FAILED,
            )

    async def test_total_token_budget_is_cumulative_across_vendor_pages(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        extractor = _Extractor(rendered)
        ocr.__dict__["_extractor"] = extractor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TokenLimitError):
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(3)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    max_tokens=2,
                ):
                    pass

        self.assertEqual(extractor.budgets, [(2, None)])

    async def test_output_token_budget_is_cumulative_across_vendor_pages(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        extractor = _Extractor(rendered)
        ocr.__dict__["_extractor"] = extractor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TokenLimitError):
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(3)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    max_output_tokens=1,
                ):
                    pass

        self.assertEqual(extractor.budgets, [(None, 1)])

    async def test_resume_keeps_geometry_for_pages_committed_before_failure(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        ocr.__dict__["_extractor"] = _Extractor(rendered)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(OCRError):
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                ):
                    pass
            self.assertTrue((root / "ocr/page_1.xml").exists())
            self.assertTrue((root / "ocr/page_pixel_sizes.json").exists())

            ocr.__dict__["_extractor"] = _Extractor(rendered, fail_pages=())
            events = [
                event
                async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                )
            ]

            self.assertIn(OCREventKind.SKIP, [event.kind for event in events])
            self.assertEqual(
                ocr.last_page_pixel_sizes,
                {1: (12, 16), 2: (12, 16), 3: (12, 16)},
            )

    async def test_ignored_pdf_fallback_checkpoints_geometry_immediately(self):
        class FailingDocument(_Document):
            pages_count = 2

            def render_page(self, page_index: int, dpi: int) -> Image.Image:
                del dpi
                raise PDFError("render failed", page_index)

        class FailingHandler:
            def open(self, _pdf_path: Path) -> FailingDocument:
                return FailingDocument([])

        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, FailingHandler()),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(PDFError):
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    ignore_pdf_errors=lambda error: error.page_index == 1,
                ):
                    pass

            self.assertTrue((root / "ocr/page_1.xml").exists())
            self.assertEqual(ocr.last_page_pixel_sizes, {1: (100, 100)})
            self.assertTrue((root / "ocr/page_pixel_sizes.json").exists())

    async def test_extraction_interrupts_bypass_ocr_ignore_and_fallback(self):
        cases = (
            (AbortError, None, False),
            (AbortError, None, True),
            (TokenLimitError, 100, False),
            (TokenLimitError, 100, True),
        )
        for error_type, max_tokens, ignore_errors in cases:
            with self.subTest(
                error_type=error_type.__name__,
                max_tokens=max_tokens,
                ignore_errors=ignore_errors,
            ):
                rendered: list[int] = []
                ocr = OCR(
                    DeepSeekOCRVendorConfig(
                        base_url="https://example.invalid/v1",
                        api_key="key",
                        model="model",
                    ),
                    cast(Any, _Handler(rendered)),
                )
                ocr.__dict__["_extractor"] = _Extractor(
                    rendered,
                    fail_pages=(),
                    errors={1: error_type()},
                )
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    with self.assertRaises(error_type):
                        async for _ in ocr.recognize_vendor(
                            ConcurrentExecutor(FixedCapacity(1)),
                            pdf_path=root / "source.pdf",
                            asset_path=root / "assets",
                            ocr_path=root / "ocr",
                            ignore_ocr_errors=ignore_errors,
                            max_tokens=max_tokens,
                        ):
                            pass
                    self.assertFalse((root / "ocr/page_1.xml").exists())
                    self.assertFalse((root / "ocr/page_1.failed").exists())


if __name__ == "__main__":
    unittest.main()
