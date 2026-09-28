import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, cast

from PIL import Image

from pdf_craft import ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity
from pdf_craft.error import OCRError
from pdf_craft.pdf.ocr import OCR, OCREventKind
from pdf_craft.pdf.types import Page


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
    def __init__(self, rendered: list[int]) -> None:
        self._rendered = rendered
        self._lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.started_after_render: list[bool] = []

    def image2page(self, *, page_index: int, **_kwargs) -> Page:
        with self._lock:
            self.started_after_render.append(self._rendered == [1, 2, 3])
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        try:
            time.sleep(0.02)
            if page_index == 2:
                raise OCRError("ignored", page_index=2, step_index=1)
            return Page(page_index, None, [], [], page_index, page_index)
        finally:
            with self._lock:
                self.active -= 1


class VendorOCRConcurrencyTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
