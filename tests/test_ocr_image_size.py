"""Actual PNG budgets, bounded retries, and upload/coordinate contracts."""
# pylint: disable=protected-access,cell-var-from-loop
import json
import random
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from xml.etree import ElementTree

from PIL import Image
from doc_page_extractor.extraction_context import ExtractionAbortedError

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRLocalConfig, DeepSeekOCRVendorConfig, ErrorPolicy,
    ExtractionOptions, FixedCapacity, OperationError, UnlimitedOCRVendorConfig,
)
from pdf_craft.error import NoUsableOCRPagesError, PDFError
from pdf_craft.pdf.handler import _MaterializedPDFHandler
from pdf_craft.pdf.ocr import OCR, OCREventKind, _PreparedVendorPage, _decode_vendor_response, _prepare_second_stage
from pdf_craft.pdf.page_ref import PageRef
from pdf_craft.pdf.png import bounded_png, resized_png
from pdf_craft.pdf.types import Page, decode
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRResponse, VendorOCRRuntime


def noisy(size):
    return Image.frombytes("RGB", size, random.Random(919).randbytes(size[0] * size[1] * 3))


def png_size(image):
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return buffer.tell()


class _Document:
    pages_count = 1

    def __init__(self, fixed=False, white=False):
        self.calls = []
        self.fixed = fixed
        self.white = white

    def render_page(self, page_index, dpi):
        assert page_index == 1
        self.calls.append(dpi)
        size = (300, 360) if self.fixed else (dpi, round(dpi * 1.2))
        return Image.new("RGB", size, "white") if self.white else noisy(size)

    def close(self):
        pass


class _Handler:
    def __init__(self, document):
        self.document = document

    def open(self, _path):
        return self.document


def config():
    return DeepSeekOCRVendorConfig("https://example.invalid", "unused", "unused", retry_times=0)


class PNGBudgetTests(TestCase):
    def test_measured_feedback_from_initial_dpi_and_owned_file(self):
        doc = _Document()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "page.png"
            image = PageRef(cast(Any, doc), 1).render(300, 70_000, png_path=path)
            try:
                self.assertEqual(doc.calls[0], 300)
                self.assertGreater(len(doc.calls), 1)
                self.assertLessEqual(len(doc.calls), 5)
                self.assertTrue(all(a > b for a, b in zip(doc.calls, doc.calls[1:])))
                self.assertLessEqual(path.stat().st_size, 70_000)
                with Image.open(path) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, image.size)
            finally:
                image.close()

    def test_equality_and_unlimited_keep_initial_quality(self):
        with noisy((300, 360)) as sample:
            size = png_size(sample)
        for limit in (None, size, size + 1):
            with self.subTest(limit=limit):
                doc = _Document()
                image = PageRef(cast(Any, doc), 1).render(300, limit)
                image.close()
                self.assertEqual(doc.calls, [300])
        doc = _Document()
        image = PageRef(cast(Any, doc), 1).render(300, size - 1)
        image.close()
        self.assertGreater(len(doc.calls), 1)

    def test_compressible_page_does_not_trigger_estimated_downsampling(self):
        doc = _Document(white=True)
        image = PageRef(cast(Any, doc), 1).render(300, 2000)
        image.close()
        self.assertEqual(doc.calls, [300])

    def test_five_attempts_and_minimum_resolution_stop(self):
        with noisy((300, 360)) as sample:
            limit = png_size(sample) - 1
        for dpi, expected in ((300, 5), (1, 1)):
            doc = _Document(fixed=True)
            with self.subTest(dpi=dpi), self.assertRaises(PDFError) as raised:
                PageRef(cast(Any, doc), 1).render(dpi, limit)
            self.assertEqual(len(doc.calls), expected)
            self.assertEqual(raised.exception.page_index, 1)
            self.assertIn(f"after {expected} generation(s)", str(raised.exception.__cause__))

    def test_repeated_feedback_can_succeed_on_fifth_attempt(self):
        calls = []
        def render(dpi):
            calls.append(dpi)
            return noisy((100, 100)) if len(calls) < 5 else Image.new("RGB", (100, 100), "white")
        with noisy((100, 100)) as sample:
            limit = png_size(sample) - 1
        image = bounded_png(render, 300, limit)
        image.close()
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(a > b for a, b in zip(calls, calls[1:])))

    def test_invalid_limits_do_not_render(self):
        for limit in (0, -1, True, 1.5):
            doc = _Document()
            with self.subTest(limit=limit), self.assertRaises(PDFError):
                PageRef(cast(Any, doc), 1).render(300, cast(Any, limit))
            self.assertEqual(doc.calls, [])
            with self.assertRaises(ValueError):
                ExtractionOptions(max_page_image_file_size=cast(Any, limit))

    def test_cancel_after_render_closes_image_and_preserves_cancellation(self):
        images = []
        def render(_dpi):
            image = noisy((10, 10))
            images.append(image)
            return image
        with self.assertRaises(ExtractionAbortedError):
            bounded_png(render, 300, 100, aborted=lambda: bool(images))
        with self.assertRaises(ValueError):
            images[0].getpixel((0, 0))

    def test_encoding_exception_closes_candidate(self):
        image = noisy((10, 10))
        with patch.object(Image.Image, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                bounded_png(lambda _dpi: image, 300, 100)
        with self.assertRaises(ValueError):
            image.getpixel((0, 0))

    def test_materialized_handler_retries_from_original_raster(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with noisy((300, 360)) as image:
                image.save(root / "page-1.png")
            handler = _MaterializedPDFHandler(root, 1, cast(Any, None), {1: (1, 1.2)}, 300)
            doc = handler.open(root / "source.pdf")
            image = PageRef(doc, 1).render(300, 70_000)
            try:
                self.assertLessEqual(png_size(image), 70_000)
                self.assertLess(image.width, 300)
            finally:
                image.close()

    def test_local_ocr_receives_same_measured_raster(self):
        doc, received = _Document(), []
        ocr = OCR(DeepSeekOCRLocalConfig(), cast(Any, _Handler(doc)))
        def extract(**kwargs):
            image = kwargs["image"]
            received.append(png_size(image))
            return Page(1, None, [], [], 0, 0)
        with TemporaryDirectory() as folder, patch.object(ocr._extractor, "image2page", extract):
            root = Path(folder)
            events = list(ocr.recognize(pdf_path=root / "input.pdf", asset_path=root / "assets",
                                        ocr_path=root / "ocr", dpi=300, max_page_image_file_size=70_000))
        self.assertEqual(doc.calls[0], 300)
        self.assertLessEqual(received[0], 70_000)
        self.assertEqual(events[-1].kind, OCREventKind.COMPLETE)


class VendorImageBudgetTests(IsolatedAsyncioTestCase):
    async def test_final_resolver_sees_bounded_file_and_geometry_in_both_schedules(self):
        for budget in (None, 10000):
            doc = _Document()
            resolved = []
            async def resolve(path):
                with Image.open(path) as image:
                    image.load()
                    resolved.append((path.stat().st_size, image.size))
                return "https://images.invalid/page.png"
            async def request(_runtime, _value, started=None):
                if started is not None:
                    started.set()
                return VendorOCRResponse({}, raw_text="<|ref|>text<|/ref|><|det|>[[100, 100, 900, 900]]<|/det|>recognized body")
            with self.subTest(budget=budget), TemporaryDirectory() as folder, patch.object(
                VendorOCRRuntime, "_request_deepseek", request,
            ):
                root = Path(folder)
                ocr = OCR(config(), cast(Any, _Handler(doc)))
                events = [event async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)), image_url_resolver=resolve,
                    pdf_path=root / "source.pdf", asset_path=root / "assets", ocr_path=root / "ocr",
                    dpi=300, max_page_image_file_size=70_000, max_tokens=budget,
                    includes_footnotes=True,
                )]
                self.assertEqual(doc.calls[0], 300)
                self.assertLessEqual(len(doc.calls), 5)
                self.assertEqual(len(resolved), 2)
                self.assertTrue(all(size <= 70_000 for size, _ in resolved))
                sizes = json.loads((root / "ocr/page_pixel_sizes.json").read_text())
                self.assertEqual(tuple(sizes["1"]), resolved[0][1])
                self.assertEqual(events[-1].kind, OCREventKind.COMPLETE)

    async def test_exhaustion_uses_render_policy_without_upload(self):
        doc = _Document(fixed=True)
        with noisy((300, 360)) as image:
            limit = png_size(image) - 1
        resolved, failures = [], []
        async def resolve(path):
            resolved.append(path)
            return "https://images.invalid/page.png"
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ocr = OCR(config(), cast(Any, _Handler(doc)))
            with self.assertRaises(NoUsableOCRPagesError):
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)), image_url_resolver=resolve,
                    pdf_path=root / "source.pdf", asset_path=root / "assets", ocr_path=root / "ocr",
                    dpi=300, max_page_image_file_size=limit,
                    error_policy=ErrorPolicy(ignore_pdf_render_errors=lambda failure: failures.append(failure) or True),
                ):
                    pass
            self.assertEqual(len(doc.calls), 5)
            self.assertEqual(resolved, [])
            self.assertEqual(len(failures), 1)

    async def test_guard_rejects_oversized_final_file_before_resolver(self):
        calls = []
        async def resolve(path):
            calls.append(path)
            return "https://images.invalid/page.png"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "page.png"
            path.write_bytes(b"too large")
            runtime = VendorOCRRuntime(config(), ConcurrentExecutor(FixedCapacity(1)), resolve,
                                       max_image_file_size=4)
            with self.assertRaises(OperationError):
                await runtime._resolve_image_url(VendorOCRInput(1, path, lambda: False))
        self.assertEqual(calls, [])

    async def test_second_stage_resize_keeps_saved_footnotes_in_source_geometry(self):
        for budget in (None, 10000):
            doc, received = _Document(white=True), []
            async def resolve(path):
                with Image.open(path) as image:
                    received.append(image.size)
                self.assertLessEqual(path.stat().st_size, 70_000)
                return "https://images.invalid/page.png"
            async def request(_runtime, value, started=None):
                if started is not None:
                    started.set()
                bbox = "100, 100, 900, 600" if value.stage_index == 1 else "100, 800, 900, 950"
                return VendorOCRResponse({}, raw_text=f"<|ref|>text<|/ref|><|det|>[[{bbox}]]<|/det|>probe")
            def redact(**kwargs):
                return noisy(kwargs["image"].size)
            with self.subTest(budget=budget), TemporaryDirectory() as folder, patch.object(
                VendorOCRRuntime, "_request_deepseek", request,
            ), patch("doc_page_extractor.redacter.redact", redact):
                root = Path(folder)
                ocr = OCR(config(), cast(Any, _Handler(doc)))
                async for _ in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(1)), image_url_resolver=resolve,
                    pdf_path=root / "source.pdf", asset_path=root / "assets", ocr_path=root / "ocr",
                    dpi=300, max_page_image_file_size=70_000, max_tokens=budget, includes_footnotes=True,
                ):
                    pass
                page = decode(ElementTree.parse(root / "ocr/page_1.xml").getroot())
                self.assertEqual(doc.calls, [300])
                self.assertEqual(received[0], (300, 360))
                self.assertLess(received[1][0], 300)
                self.assertEqual(len(page.footnotes_layouts), 1)
                for actual, expected in zip(page.footnotes_layouts[0].det, (30, 288, 270, 342)):
                    self.assertAlmostEqual(actual, expected, delta=2)

    async def test_resized_second_stage_maps_back_to_first_stage_coordinates(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / "page.png"
            with Image.new("RGB", (300, 360), "white") as image:
                image.save(original)
            item = _PreparedVendorPage(1, original, original, 0, 1, max_image_file_size=30_000)
            # Simulate a redaction whose encoding is larger than the first PNG.
            # The genuine byte limiter must still bound the derived request.
            def redact(**_kwargs):
                return noisy((300, 360))
            with patch("doc_page_extractor.redacter.redact", redact):
                second = _prepare_second_stage(item, SimpleNamespace(layouts=[]), root, lambda: False)
            self.assertLessEqual(second.request_path.stat().st_size, 30_000)
            self.assertGreater(second.scale_x, 1)
            response = VendorOCRResponse({}, raw_text="<|ref|>text<|/ref|><|det|>[[100, 800, 900, 950]]<|/det|>footnote")
            result = _decode_vendor_response(config(), second, response)
            self.assertEqual(len(result.layouts), 1)
            left, top, right, bottom = result.layouts[0].det
            self.assertAlmostEqual(left, 30, delta=2)
            self.assertAlmostEqual(top, 288, delta=2)
            self.assertAlmostEqual(right, 270, delta=2)
            self.assertAlmostEqual(bottom, 342, delta=2)

    async def test_vendor_dimension_resize_obeys_bytes_and_keeps_aspect(self):
        with TemporaryDirectory() as folder, noisy((8300, 12)) as image:
            path = Path(folder) / "request.png"
            size = resized_png(image, path, 70_000, lambda: False, maximum=8192)
            self.assertLessEqual(max(size), 8192)
            self.assertLessEqual(path.stat().st_size, 70_000)
            self.assertAlmostEqual(size[0] / image.width, size[1] / image.height, delta=0.05)

    async def test_unlimited_request_dimensions_and_coordinates(self):
        doc, received = _Document(), []
        def render(page_index, dpi):
            self.assertEqual(page_index, 1)
            doc.calls.append(dpi)
            return noisy((8300, 12))
        async def resolve(path):
            with Image.open(path) as image:
                received.append(image.size)
            self.assertLessEqual(path.stat().st_size, 1_000_000)
            return "https://images.invalid/page.png"
        async def request(_runtime, _value, started=None):
            if started is not None:
                started.set()
            return VendorOCRResponse({"parse_result": {"pages": [{"layouts": [{
                "type": "text", "text": "probe", "position": [100, 2, 200, 6],
            }]}]}})
        with TemporaryDirectory() as folder, patch.object(doc, "render_page", render), patch.object(
            VendorOCRRuntime, "_request_unlimited", request,
        ):
            root = Path(folder)
            ocr = OCR(UnlimitedOCRVendorConfig("unused", "unused"), cast(Any, _Handler(doc)))
            async for _ in ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(1)), image_url_resolver=resolve,
                pdf_path=root / "input.pdf", asset_path=root / "assets", ocr_path=root / "ocr",
                dpi=300, max_page_image_file_size=1_000_000,
            ):
                pass
            page = decode(ElementTree.parse(root / "ocr/page_1.xml").getroot())
            self.assertEqual(received, [(8192, 12)])
            self.assertEqual(doc.calls, [300])
            self.assertEqual(page.body_layouts[0].det, (101, 2, 304, 8))
            geometry = json.loads((root / "ocr/page_pixel_sizes.json").read_text())
            self.assertEqual(geometry["1"], [8300, 12])
