"""Opt-in, offline reproduction using the privately retained production PDF."""
# pylint: disable=protected-access
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from xml.etree import ElementTree

from PIL import Image

from pdf_craft import ConcurrentExecutor, FixedCapacity
from pdf_craft.pdf.handler import DefaultPDFDocument
from pdf_craft.pdf.ocr import OCR, OCREventKind
from pdf_craft.pdf.types import decode
from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRRuntime
from tests.test_ocr_image_size import config, png_size


@skipUnless(os.environ.get("PDF_CRAFT_IMAGE_SIZE_REGRESSION_PDF"), "private real-PDF gate is opt-in")
class RealPDFImageBudgetTests(IsolatedAsyncioTestCase):
    async def test_production_page_reaches_resolver_below_limit(self):
        source = Path(os.environ["PDF_CRAFT_IMAGE_SIZE_REGRESSION_PDF"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         "b9bf53eb0080c6258bb8e2f785f31b33ecbd8fcfb1649656e9e20f8e2c477458")
        archive = source.parent / "oversized-page-1.png"
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                         "193f9c2342c3515004bede38b9dda7c0bfa2a54ae8620118279143612030595f")
        self.assertGreater(archive.stat().st_size, 10_000_000)
        with Image.open(archive) as image:
            image.load()
            self.assertEqual(image.size, (4450, 6849))

        traces, uploaded = [], []
        render = DefaultPDFDocument.render_page
        def measured_render(document, page_index, dpi):
            image = render(document, page_index, dpi)
            traces.append({"page": page_index, "dpi": dpi, "pixels": image.size,
                           "png_bytes": png_size(image)})
            return image

        async def resolve(path):
            with Image.open(path) as image:
                image.load()
                uploaded.append({"bytes": path.stat().st_size, "pixels": image.size})
                output = os.environ.get("PDF_CRAFT_IMAGE_SIZE_REGRESSION_OUTPUT")
                if output:
                    target = Path(output)
                    target.mkdir(parents=True, exist_ok=True)
                    image.thumbnail((650, 1000))
                    image.save(target / "final-preview.png")
            self.assertLessEqual(uploaded[-1]["bytes"], 10_000_000)
            return "https://images.invalid/production-page.png"

        async def request(_runtime, _value, started=None):
            if started is not None:
                started.set()
            return VendorOCRResponse({}, raw_text=(
                "<|ref|>text<|/ref|><|det|>[[100, 100, 900, 900]]<|/det|>geometry probe"
            ))

        with TemporaryDirectory() as folder, patch.object(
            DefaultPDFDocument, "render_page", measured_render,
        ), patch.object(VendorOCRRuntime, "_request_deepseek", request):
            root = Path(folder)
            ocr = OCR(config(), None)
            events = [event async for event in ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(1)), image_url_resolver=resolve, pdf_path=source,
                asset_path=root / "assets", ocr_path=root / "ocr", page_indexes={1},
                dpi=300, max_page_image_file_size=10_000_000,
            )]
            self.assertEqual(traces[0]["dpi"], 300)
            self.assertGreater(traces[0]["png_bytes"], 10_000_000)
            self.assertGreater(len(traces), 1)
            self.assertLessEqual(len(traces), 5)
            self.assertTrue(all(a["dpi"] > b["dpi"] for a, b in zip(traces, traces[1:])))
            self.assertEqual(len(uploaded), 1)
            self.assertEqual(uploaded[0]["bytes"], traces[-1]["png_bytes"])
            pixels = uploaded[0]["pixels"]
            geometry = json.loads((root / "ocr/page_pixel_sizes.json").read_text())
            self.assertEqual(tuple(geometry["1"]), pixels)
            page = decode(ElementTree.parse(root / "ocr/page_1.xml").getroot())
            self.assertEqual(len(page.body_layouts), 1)
            self.assertEqual(page.body_layouts[0].det,
                             tuple(round(value * dimension) for value, dimension in
                                   zip((0.1, 0.1, 0.9, 0.9), (*pixels, *pixels))))
            self.assertTrue(any(event.kind == OCREventKind.COMPLETE for event in events))
            self.assertFalse(any(event.kind == OCREventKind.FAILED for event in events))
            # Optional local artifacts support a visual comparison without
            # adding any customer bytes to the public test fixtures.
            output = os.environ.get("PDF_CRAFT_IMAGE_SIZE_REGRESSION_OUTPUT")
            if output:
                target = Path(output)
                target.mkdir(parents=True, exist_ok=True)
                (target / "trace.json").write_text(json.dumps({"generations": traces, "resolver": uploaded}, indent=2))
                with Image.open(archive) as original:
                    original.thumbnail((650, 1000))
                    original.save(target / "original-preview.png")
        print("REAL_PDF_IMAGE_SIZE_GATE", json.dumps({"generations": traces, "resolver": uploaded}))
