"""A proven white render must not acquire text through an OCR request."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree.ElementTree import parse

from PIL import Image

from pdf_craft import ConcurrentExecutor, DeepSeekOCRLocalConfig, DeepSeekOCRVendorConfig, FixedCapacity
from pdf_craft.pdf.ocr import OCR, OCREventKind
from pdf_craft.pdf.types import Page, PageLayout, decode, encode
from pdf_craft.common import save_xml
from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRResult, VendorOCRRuntime


class _Document:
    def __init__(self, images):
        self.images = images
        self.pages_count = len(images)
        self.rendered = []

    def page_size(self, index):
        return self.images[index - 1].size

    def render_page(self, page_index, dpi):
        del dpi
        self.rendered.append(page_index)
        return self.images[page_index - 1].copy()

    def close(self):
        pass


class _Runtime:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def request(self, request, **_kwargs):
        self.calls.append(request.page_index)
        return VendorOCRResponse({}, input_tokens=7, output_tokens=3)

    async def request_many(self, requests, **_kwargs):
        async for request in requests:
            yield VendorOCRResult(request, await self.request(request))


def _finish(item, _results, _hub, _notes, _plots, input_tokens, output_tokens, _aborted):
    return Page(item.page_index, None,
                [PageLayout("text", (1, 1, 10, 10), "visible", 0, None)], [],
                input_tokens, output_tokens)


class BlankOCRPagesTests(unittest.IsolatedAsyncioTestCase):
    async def _vendor(self, images, root, *, max_tokens=None, callback=None,
                      footnotes=False):
        document = _Document(images)
        handler = Mock()
        handler.open.return_value = document
        ocr = OCR(DeepSeekOCRVendorConfig("https://unused.invalid", "unused", "unused"), handler)
        runtime = _Runtime()

        async def consume():
            with patch("pdf_craft.pdf.ocr.VendorOCRRuntime", return_value=runtime), \
                    patch("pdf_craft.pdf.ocr._parse_vendor_response", return_value=None), \
                    patch.object(OCR, "_finish_vendor_results", side_effect=_finish):
                return [event async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(2)), pdf_path=root / "unused.pdf",
                    ocr_path=root / "ocr", asset_path=root / "assets",
                    cover_path=root / "cover.png", max_tokens=max_tokens,
                    includes_footnotes=footnotes,
                    on_preparation_event=callback)]

        events = await asyncio.wait_for(consume(), 5)
        return events, runtime.calls, document.rendered

    async def test_all_white_pages_skip_batch_and_budgeted_requests(self):
        for max_tokens in (None, 100):
            for footnotes in (False, True):
                with self.subTest(max_tokens=max_tokens, footnotes=footnotes), \
                        tempfile.TemporaryDirectory() as directory, \
                        Image.new("RGB", (16, 24), "white") as image:
                    root = Path(directory)
                    events, calls, _ = await self._vendor([image, image], root,
                        max_tokens=max_tokens, footnotes=footnotes)
                    self.assertEqual(calls, [])
                    complete = [event for event in events if event.kind == OCREventKind.COMPLETE]
                    self.assertEqual([event.page_index for event in complete], [1, 2])
                    self.assertTrue(all(event.input_tokens == event.output_tokens == 0 for event in complete))
                    for index in (1, 2):
                        page = decode(parse(root / "ocr" / f"page_{index}.xml").getroot())
                        self.assertEqual(page.body_layouts, [])
                        self.assertEqual(page.footnotes_layouts, [])
                    self.assertTrue((root / "cover.png").exists())
                    self.assertTrue((root / "ocr" / "done").exists())
                    self.assertEqual(json.loads((root / "ocr" / "page_pixel_sizes.json").read_text()),
                                     {"1": [16, 24], "2": [16, 24]})
                    _, resumed_calls, rendered = await self._vendor([image, image], root)
                    self.assertEqual(resumed_calls, [])
                    self.assertEqual(rendered, [])

    async def test_one_near_white_pixel_keeps_real_ocr_path(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as white:
            with white.copy() as faint:
                faint.putpixel((0, 0), (254, 255, 255))
                root = Path(directory)
                events, calls, _ = await self._vendor([white, faint], root)
                self.assertEqual(calls, [2])
                self.assertEqual(sorted(event.page_index for event in events
                                        if event.kind == OCREventKind.COMPLETE), [1, 2])
                page = decode(parse(root / "ocr" / "page_2.xml").getroot())
                self.assertEqual(page.body_layouts[0].text, "visible")

    async def test_blank_complete_callback_observes_persisted_page(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as image:
            root = Path(directory)
            completed = []

            def callback(event):
                if event.kind == OCREventKind.COMPLETE:
                    self.assertTrue((root / "ocr" / f"page_{event.page_index}.xml").exists())
                    completed.append(event.page_index)

            _, calls, _ = await self._vendor([image], root, callback=callback)
            self.assertEqual(calls, [])
            self.assertEqual(completed, [1])

    async def test_existing_ocr_cache_is_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as image:
            root = Path(directory)
            cache = root / "ocr"
            cache.mkdir()
            path = cache / "page_1.xml"
            save_xml(encode(Page(1, None, [PageLayout("text", (0, 0, 16, 24), "1", 0, None)],
                                 [], 7, 3)), path)
            (cache / "page_pixel_sizes.json").write_text('{"1": [16, 24]}')
            before = path.read_bytes()
            events, calls, rendered = await self._vendor([image], root)
            self.assertEqual(calls, [])
            self.assertEqual(rendered, [])
            self.assertEqual(path.read_bytes(), before)
            self.assertIn(OCREventKind.SKIP, [event.kind for event in events])

    async def test_white_retry_clears_previous_failure_marker(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as image:
            root = Path(directory)
            cache = root / "ocr"
            cache.mkdir()
            marker = cache / "page_1.failed"
            marker.write_text("OCRError")
            _, calls, _ = await self._vendor([image], root)
            self.assertEqual(calls, [])
            self.assertFalse(marker.exists())

    async def test_blank_callback_error_propagates(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as image:
            def callback(event):
                if event.kind == OCREventKind.COMPLETE:
                    raise RuntimeError("blank observer failed")
            with self.assertRaisesRegex(RuntimeError, "blank observer failed"):
                await self._vendor([image], Path(directory), callback=callback)

    async def test_grayscale_and_opaque_white_alpha_are_proven_empty(self):
        for mode in ("L", "RGBA"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory, \
                    Image.new(mode, (16, 24), "white") as image:
                _, calls, _ = await self._vendor([image], Path(directory))
                self.assertEqual(calls, [])

    async def test_white_second_pass_skips_request_without_losing_first_pass(self):
        for budget in (None, 100):
            for faint in (False, True):
                with self.subTest(budget=budget, faint=faint), \
                        tempfile.TemporaryDirectory() as directory, \
                        Image.new("RGB", (16, 24), "white") as image:
                    image.putpixel((0, 0), (0, 0, 0))

                    def prepare(item, _result, root, _aborted, *, size=image.size, faint=faint):
                        path = root / f"page_{item.page_index}_stage_2.png"
                        with Image.new("RGB", size, "white") as masked:
                            if faint:
                                masked.putpixel((0, 0), (254, 255, 255))
                            masked.save(path)
                        return replace(item, request_path=path)

                    root = Path(directory)
                    with patch("pdf_craft.pdf.ocr._prepare_second_stage", side_effect=prepare):
                        _, calls, _ = await self._vendor([image, image], root,
                            max_tokens=budget, footnotes=True)
                    self.assertEqual(len(calls), 4 if faint else 2)
                    for index in (1, 2):
                        page = decode(parse(root / "ocr" / f"page_{index}.xml").getroot())
                        self.assertEqual(page.body_layouts[0].text, "visible")
                        self.assertEqual(page.input_tokens, 14 if faint else 7)

    async def test_real_mask_and_decoder_keep_body_without_second_request(self):
        for budget in (None, 100):
            with self.subTest(budget=budget), tempfile.TemporaryDirectory() as directory, \
                    Image.new("RGB", (16, 24), "white") as image:
                image.putpixel((2, 2), (0, 0, 0))
                root = Path(directory)
                handler = Mock()
                handler.open.return_value = _Document([image])
                calls = []

                async def request(_runtime, value, started=None, *, record=calls.append):
                    if started is not None:
                        started.set()
                    record(value.stage_index)
                    return VendorOCRResponse({}, raw_text=(
                        "<|ref|>text<|/ref|><|det|>[[0, 0, 1000, 500]]<|/det|>這是正文。"
                    ), input_tokens=7, output_tokens=3)

                ocr = OCR(DeepSeekOCRVendorConfig("https://unused.invalid", "unused", "unused"), handler)
                with patch.object(VendorOCRRuntime, "_request_deepseek", new=request):
                    events = [event async for event in ocr.recognize_vendor(
                        ConcurrentExecutor(FixedCapacity(2)), pdf_path=root / "unused.pdf",
                        ocr_path=root / "ocr", asset_path=root / "assets",
                        includes_footnotes=True, max_tokens=budget)]
                self.assertEqual(calls, [1])
                self.assertEqual(events[-1].kind, OCREventKind.COMPLETE)
                page = decode(parse(root / "ocr" / "page_1.xml").getroot())
                self.assertEqual(page.body_layouts[0].text, "這是正文。")
                self.assertEqual(page.footnotes_layouts, [])
                self.assertEqual((page.input_tokens, page.output_tokens), (7, 3))

    def test_local_backend_also_skips_white_page(self):
        with tempfile.TemporaryDirectory() as directory, Image.new("RGB", (16, 24), "white") as image:
            root = Path(directory)
            handler = Mock()
            handler.open.return_value = _Document([image])
            ocr = OCR(DeepSeekOCRLocalConfig(), handler)
            extractor = Mock()
            extractor.image2page.return_value = Page(1, None, [], [], 1, 1)
            with patch.object(ocr, "_extractor", extractor):
                events = list(ocr.recognize(root / "unused.pdf", root / "assets", root / "ocr"))
            extractor.image2page.assert_not_called()
            self.assertEqual(events[-1].kind, OCREventKind.COMPLETE)
            self.assertEqual(events[-1].input_tokens, 0)


if __name__ == "__main__":
    unittest.main()
