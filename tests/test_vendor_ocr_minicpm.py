# pylint: disable=protected-access
import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from doc_page_extractor.structure import build_structured_page
from doc_page_extractor.types import LayoutKind
from PIL import Image

from pdf_craft import (
    ConcurrentExecutor,
    FixedCapacity,
    MiniCPMOCRVendorConfig,
)
from pdf_craft.pdf.minicpm_ocr import parse_minicpm_ocr_layouts
from pdf_craft.pdf.ocr import OCR, _supports_vendor_stages
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.vendor_ocr import (
    VendorOCRInput,
    VendorOCRRuntime,
)


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
    def test_config_is_a_single_stage_vendor_backend(self):
        config = MiniCPMOCRVendorConfig("https://model.invalid/v1")

        self.assertTrue(OCR(config, None).is_vendor)
        self.assertFalse(_supports_vendor_stages(config))

    def test_page_extractor_rejects_vendor_config_with_clear_error(self):
        node = PageExtractorNode(
            MiniCPMOCRVendorConfig("https://model.invalid/v1"),
        )

        # pylint: disable=protected-access
        with self.assertRaisesRegex(TypeError, "vendor OCR transport"):
            node._get_page_extractor()

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

        self.assertEqual(response.raw_text, "")
        self.assertEqual((response.input_tokens, response.output_tokens), (5, 6))


class MiniCPMOCRLayoutTests(unittest.TestCase):
    def test_blocks_become_page_layouts_in_reading_order(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (200, 300), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "image_width": 100,
                    "image_height": 150,
                    "blocks": [
                        _block(2, "text", (10, 40, 90, 100), "Body"),
                        _block(0, "doc_title", (10, 10, 90, 30), "Title"),
                        _block(1, "header", (10, 0, 90, 8), "Running head"),
                        _block(3, "number", (40, 145, 60, 150), "12"),
                        _block(4, "formula_number", (40, 145, 60, 150), "(1.1)"),
                        _block(5, "image", (10, 100, 50, 140), "",
                               status="skipped_no_vlm"),
                        _block(6, "figure_title", (50, 100, 90, 120), "Figure 1"),
                        _block(7, "table", (10, 120, 90, 140), "<table></table>"),
                        _block(8, "display_formula", (10, 120, 90, 130), "x^2"),
                        _block(9, "footnote", (10, 130, 90, 149), "note"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(
            [layout.kind for layout in layouts],
            [
                LayoutKind.TITLE,
                LayoutKind.HEADER,
                LayoutKind.TEXT,
                LayoutKind.PAGE_NUMBER,
                LayoutKind.PAGE_NUMBER,
                LayoutKind.IMAGE,
                LayoutKind.IMAGE_CAPTION,
                LayoutKind.TABLE,
                LayoutKind.EQUATION,
                LayoutKind.FOOTNOTE,
            ],
        )
        self.assertEqual(layouts[0].det, (20, 20, 180, 60))
        self.assertEqual(layouts[5].det, (20, 200, 100, 280))
        self.assertEqual(layouts[7].html, "<table></table>")
        self.assertEqual(layouts[8].text, "x^2")
        self.assertTrue(all(
            layout.source == "minicpm-ocr-vendor" for layout in layouts
        ))
        structured = build_structured_page(layouts)
        self.assertEqual(
            [block.kind for block in structured.blocks],
            [
                LayoutKind.TITLE,
                LayoutKind.TEXT,
                LayoutKind.IMAGE,
                LayoutKind.TABLE,
                LayoutKind.EQUATION,
                LayoutKind.FOOTNOTE,
            ],
        )
        self.assertEqual(
            [layout.kind for layout in structured.ignored],
            [LayoutKind.HEADER, LayoutKind.PAGE_NUMBER, LayoutKind.PAGE_NUMBER],
        )

    def test_vision_footnote_attaches_to_the_image_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "minicpm_ocr": {
                    "blocks": [
                        _block(0, "image", (0, 0, 5, 5), "",
                               status="skipped_no_vlm"),
                        _block(1, "vision_footnote", (0, 5, 10, 8), "图 1 说明"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[1].kind, LayoutKind.IMAGE_CAPTION)
        structured = build_structured_page(layouts)
        self.assertEqual(len(structured.blocks), 1)
        self.assertEqual(structured.blocks[0].kind, LayoutKind.IMAGE)
        self.assertEqual(
            [child.kind for child in structured.blocks[0].children],
            [LayoutKind.IMAGE_CAPTION],
        )

    def test_table_caption_is_separated_from_figure_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "blocks": [
                        _block(0, "figure_title", (0, 0, 10, 2), "表 1 数据"),
                        _block(1, "table", (0, 2, 10, 8), "<table></table>"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[0].kind, LayoutKind.TABLE_CAPTION)
        structured = build_structured_page(layouts)
        self.assertEqual(structured.blocks[0].kind, LayoutKind.TABLE)
        self.assertEqual(
            [child.kind for child in structured.blocks[0].children],
            [LayoutKind.TABLE_CAPTION],
        )

    def test_chart_caption_follows_the_chart_as_a_table_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "blocks": [
                        _block(0, "chart", (0, 0, 10, 5), "<table></table>"),
                        _block(1, "figure_title", (0, 5, 10, 8), "图 1 销量"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[0].kind, LayoutKind.TABLE)
        self.assertEqual(layouts[1].kind, LayoutKind.TABLE_CAPTION)
        structured = build_structured_page(layouts)
        self.assertEqual(structured.blocks[0].kind, LayoutKind.TABLE)
        self.assertEqual(
            [child.kind for child in structured.blocks[0].children],
            [LayoutKind.TABLE_CAPTION],
        )

    def test_cjk_table_caption_without_spaces_is_recognized(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "blocks": [
                        _block(0, "table", (0, 0, 10, 8), "<table></table>"),
                        _block(1, "figure_title", (0, 8, 10, 10), "表1数据"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[1].kind, LayoutKind.TABLE_CAPTION)

    def test_blocks_without_usable_regions_are_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            with self.assertRaisesRegex(ValueError, "none had a usable region"):
                parse_minicpm_ocr_layouts({
                    "ocr": {
                        "blocks": [
                            {"order": 0, "label": "text", "content": "text"},
                        ],
                    },
                }, image_path, source="minicpm-ocr-vendor")

    def test_missing_one_image_dimension_does_not_scale_only_one_axis(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1000, 1500), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "image_width": 500,
                    "blocks": [
                        _block(0, "text", (10, 20, 30, 40), "text"),
                    ],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[0].det, (10, 20, 30, 40))

    def test_multiple_regions_are_unioned_when_bbox_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            layouts = parse_minicpm_ocr_layouts({
                "ocr": {
                    "image_width": 100,
                    "image_height": 100,
                    "blocks": [{
                        "order": 0,
                        "label": "text",
                        "content": "text",
                        "regions": [[10, 20, 30, 40], [50, 60, 70, 80]],
                    }],
                },
            }, image_path, source="minicpm-ocr-vendor")

        self.assertEqual(layouts[0].det, (10, 20, 70, 80))

    def test_missing_blocks_is_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            with self.assertRaisesRegex(ValueError, "document layout blocks"):
                parse_minicpm_ocr_layouts(
                    {"ocr": {}},
                    image_path,
                    source="minicpm-ocr-vendor",
                )

    def test_rotated_page_is_rejected_instead_of_misplacing_boxes(self):
        cases = (
            {"ocr": {
                "blocks": [_block(0, "text", (0, 0, 10, 10), "text")],
                "rotation": {"applied": True},
            }},
            {"ocr": {
                "blocks": [_block(0, "text", (0, 0, 10, 10), "text")],
                "rotation": {"applied": 1},
            }},
            {"ocr": {
                "blocks": [_block(0, "text", (0, 0, 10, 10), "text")],
                "rotation": {"applied": "true"},
            }},
            {
                "ocr": {"blocks": [_block(0, "text", (0, 0, 10, 10), "text")]},
                "rotation": {"applied": True},
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            for data in cases:
                with self.subTest(data=data), self.assertRaisesRegex(
                    ValueError, "orientation correction",
                ):
                    parse_minicpm_ocr_layouts(
                        data, image_path, source="minicpm-ocr-vendor",
                    )

    def test_missing_page_payload_is_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            with self.assertRaisesRegex(ValueError, "an 'ocr' or 'minicpm_ocr' page"):
                parse_minicpm_ocr_layouts(
                    {"choices": [{"message": {"content": "text"}}]},
                    image_path,
                    source="minicpm-ocr-vendor",
                )

    def test_empty_page_blocks_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (10, 10), "white").save(image_path)
            self.assertEqual(parse_minicpm_ocr_layouts(
                {"ocr": {"blocks": []}},
                image_path,
                source="minicpm-ocr-vendor",
            ), [])
