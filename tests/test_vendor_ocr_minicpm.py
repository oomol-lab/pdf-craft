# pylint: disable=protected-access
import base64
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from xml.etree.ElementTree import parse

import httpx
from PIL import Image

from pdf_craft import (
    ConcurrentExecutor,
    ErrorPolicy,
    FixedCapacity,
    MiniCPMOCRVendorConfig,
)
from pdf_craft.pdf.ocr import OCR, _supports_vendor_stages
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.vendor_ocr import (
    VendorOCRInput,
    VendorOCRRuntime,
)
from pdf_craft.pdf.ocr import OCREventKind
from pdf_craft.pdf.types import decode


def _response(request: httpx.Request, *, blocks=None) -> httpx.Response:
    return httpx.Response(200, request=request, json={
        "choices": [{"message": {"content": "page markdown"}}],
        "ocr": {
            "image_width": 100,
            "image_height": 150,
            "blocks": blocks or [],
        },
        "usage": {"prompt_tokens": 12, "completion_tokens": 34},
    })


def _block(
    order: int,
    label: str,
    det,
    content: str,
    status: str = "ok",
) -> dict:
    return {
        "order": order,
        "label": label,
        "bbox": list(det),
        "content": content,
        "status": status,
    }


class MiniCPMOCRRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_delivery_preserves_content_and_failed_blocks_follow_error_policy(self):
        class Document:
            pages_count = 2

            def page_size(self, _index):
                return (100, 100)

            def render_page(self, page_index, dpi):
                del page_index, dpi
                image = Image.new("RGB", (100, 100), "white")
                image.paste("black", (10, 10, 80, 20))
                return image

            def close(self):
                pass

        class Handler:
            def open(self, _path):
                return Document()

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            blocks = [
                _block(0, "display_formula", (0, 0, 70, 10), "x=1"),
                _block(1, "formula_number", (75, 0, 95, 10), "(1.1)"),
                _block(2, "image", (0, 20, 100, 40), "image A text"),
                _block(3, "figure_title", (0, 70, 100, 75), "Figure B"),
                _block(4, "image", (0, 76, 100, 99), "image B text"),
            ]
            if len(calls) == 2:
                blocks.append(_block(5, "text", (0, 10, 100, 20), "", status="failed"))
            return httpx.Response(200, request=request, json={"ocr": {"blocks": blocks}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        failures = []
        config = MiniCPMOCRVendorConfig("https://model.invalid/v1", retry_times=0)
        ocr = OCR(config, cast(Any, Handler()))
        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient", return_value=client,
        ):
            root = Path(directory)
            events = [event async for event in ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(1)), pdf_path=root / "input.pdf",
                asset_path=root / "assets", ocr_path=root / "ocr",
                error_policy=ErrorPolicy(ignore_ocr_errors=lambda error: failures.append(error) or True),
            )]
            page = decode(parse(root / "ocr/page_1.xml").getroot())
            texts = [layout.text for layout in page.body_layouts]
            self.assertIn("x=1\n(1.1)", texts)
            self.assertIn("image A text", texts)
            self.assertIn("image B text\nFigure B", texts)
            self.assertEqual(len(failures), 1)
            self.assertTrue((root / "ocr/page_2.failed").exists())
            self.assertFalse((root / "ocr/page_1.failed").exists())
            self.assertFalse((root / "ocr/done").exists())
            self.assertEqual([event.page_index for event in events
                              if event.kind == OCREventKind.FAILED], [2])

    def test_config_is_a_single_stage_vendor_backend(self):
        config = MiniCPMOCRVendorConfig("https://model.invalid/v1")

        self.assertTrue(OCR(config, None).is_vendor)
        self.assertFalse(_supports_vendor_stages(config))

    def test_page_extractor_uses_upstream_factory(self):
        node = PageExtractorNode(
            MiniCPMOCRVendorConfig("https://model.invalid/v1"),
        )

        from doc_page_extractor.types import PageExtractor
        self.assertIsInstance(node._get_page_extractor(), PageExtractor)

    async def test_request_uses_document_parse_blocks_endpoint(self):
        payloads: list[dict] = []
        headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            headers.append(request.headers)
            return _response(request, blocks=[
                _block(0, "doc_title", (1, 2, 50, 20), "Title"),
            ])

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (100, 150), "white").save(image_path)
            expected_image = image_path.read_bytes()
            runtime = VendorOCRRuntime(
                MiniCPMOCRVendorConfig(
                    base_url="https://model.invalid/v1",
                    api_key="secret",
                    model="MiniCPM-OCR-1.0",
                ),
                ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            )
            response = await runtime.request(VendorOCRInput(
                1, image_path, lambda: False,
            ))
            await runtime._client.aclose()

        self.assertEqual(
            str(headers[0]["host"]), "model.invalid",
        )
        self.assertEqual(headers[0]["authorization"], "Bearer secret")
        payload = payloads[0]
        self.assertEqual(payload["model"], "MiniCPM-OCR-1.0")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_tokens"], 8000)
        self.assertEqual(
            payload["minicpm_ocr"], {"mode": "ocr_only", "upright": False},
        )
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        image_url = content[0]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))
        self.assertEqual(
            base64.b64decode(image_url.partition(",")[2]),
            expected_image,
        )
        self.assertEqual(response.raw_text, "page markdown")
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 34))

    async def test_self_hosted_service_omits_authorization_header(self):
        seen: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers)
            return _response(request)

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            runtime = VendorOCRRuntime(
                MiniCPMOCRVendorConfig(base_url="http://127.0.0.1:8000"),
                ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            )
            await runtime.request(VendorOCRInput(
                1, image_path, lambda: False,
            ))
            await runtime._client.aclose()

        self.assertNotIn("authorization", seen[0])

    async def test_image_url_resolver_reaches_the_payload(self):
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            urls.append(payload["messages"][0]["content"][0]["image_url"]["url"])
            return _response(request)

        async def resolve(_path: Path) -> str:
            return "https://images.invalid/page.png"

        runtime = VendorOCRRuntime(
            MiniCPMOCRVendorConfig("https://model.invalid/v1", api_key="key"),
            ConcurrentExecutor(FixedCapacity(1)),
            resolve,
        )
        runtime._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        await runtime.request(VendorOCRInput(
            1, Path("unused.png"), lambda: False,
        ))
        await runtime._client.aclose()

        self.assertEqual(urls, ["https://images.invalid/page.png"])

    async def test_layout_blocks_do_not_require_markdown_choices(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json={
                "minicpm_ocr": {"blocks": []},
                "usage": {"prompt_tokens": 5, "completion_tokens": 6},
            })

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            runtime = VendorOCRRuntime(
                MiniCPMOCRVendorConfig("https://model.invalid/v1"),
                ConcurrentExecutor(FixedCapacity(1)),
            )
            runtime._client = httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
            )
            response = await runtime.request(VendorOCRInput(
                1, image_path, lambda: False,
            ))
            await runtime._client.aclose()

        self.assertIsNone(response.raw_text)
        self.assertEqual((response.input_tokens, response.output_tokens), (5, 6))


