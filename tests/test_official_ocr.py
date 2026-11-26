# pylint: disable=protected-access
import json
import tempfile
import unittest
from dataclasses import asdict, fields
from email import policy
from email.parser import BytesParser
from html import unescape
from functools import partial
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import httpx

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, DeepSeekOfficialOCRConfig, ExtractionOptions,
    FixedCapacity, OperationError, PDFOptions,
)
from pdf_craft.error import OCRBillingError, OCRFatalError
from pdf_craft.pdf.ocr import OCR, _supports_vendor_stages
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime
from pdf_craft_tool.runtime import create_ocr_config_from_env, ocr_values_from_env
from pdf_craft_tool.smoke.ocr import create_ocr_config


TEXT = ("<｜ref｜>Title<｜/ref｜><｜box｜>[[100,100,900,200]]<｜/box｜>Chapter title\n"
        "<｜ref｜>Text<｜/ref｜><｜box｜>[[100,250,900,450]]<｜/box｜>Official OCR keeps literal ｜.")


def result(**page):
    return {"pages": [{"index": 0, "text": TEXT, "finish_reason": "stop", **page}],
            "usage": {"input_tokens": 12, "output_tokens": 34}}


class OfficialOCRTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.image = Path(self.directory.name) / "page.png"
        self.image.write_bytes(b"rendered page bytes")

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def request(self, handler, config=None, resolver=None):
        config = config or DeepSeekOfficialOCRConfig("key", retry_interval_seconds=0)
        runtime = VendorOCRRuntime(config, ConcurrentExecutor(FixedCapacity(1)), resolver)
        async with runtime:
            assert runtime._client is not None
            await runtime._client.aclose()
            runtime._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return await runtime.request(VendorOCRInput(1, self.image, lambda: False))

    def test_configuration_and_cli_use_the_existing_vendor_options(self):
        config = DeepSeekOfficialOCRConfig("secret")
        self.assertEqual({field.name for field in fields(config)},
                         {"api_key", "timeout_seconds", "retry_times", "retry_interval_seconds"})
        self.assertNotIn("secret", repr(config))
        self.assertTrue(OCR(config, None).is_vendor)
        self.assertTrue(_supports_vendor_stages(config))
        with self.assertRaises(ValueError):
            DeepSeekOfficialOCRConfig("key", retry_times=-1)
        with patch.dict("os.environ", {"PDF_CRAFT_DEEPSEEK_OCR_OFFICIAL_API_KEY": "key",
                                       "PDF_CRAFT_DEEPSEEK_OCR_OFFICIAL_TIMEOUT_SECONDS": "120"}, clear=True):
            cli = create_ocr_config_from_env("deepseek-ocr-official")
            smoke = create_ocr_config("deepseek-ocr-official", ocr_values_from_env("deepseek-ocr-official"))
        self.assertEqual(cli, DeepSeekOfficialOCRConfig("key", timeout_seconds=120))
        self.assertEqual(asdict(cli), asdict(smoke))

    async def test_multipart_uses_original_bytes_and_never_calls_url_resolver(self):
        async def resolver(_path):
            raise AssertionError("Official OCR must not resolve or encode an image URL")

        def handler(request):
            self.assertEqual(str(request.url), "https://api.deepseek.com/v1/tools/ocr")
            self.assertEqual(request.headers["authorization"], "Bearer key")
            parts = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content,
            )
            values = {part.get_param("name", header="content-disposition"): part for part in parts.iter_parts()}
            self.assertEqual(set(values), {"file", "purpose"})
            self.assertEqual(values["file"].get_content(), self.image.read_bytes())
            self.assertEqual(values["purpose"].get_content(), "rich_layout_recognition")
            return httpx.Response(200, text="\n\n" + json.dumps(result()))

        response = await self.request(handler, resolver=resolver)
        self.assertEqual(response.raw_text, TEXT)
        self.assertEqual(response.data["pages"][0]["text"], TEXT)
        self.assertEqual((response.input_tokens, response.output_tokens), (12, 34))

    async def test_temporary_errors_share_the_existing_retry_budget(self):
        responses = [
            {"error": {"message": "The OCR service is temporarily unavailable. Please try again later."}},
            {"error": {"message": "Internal Server Error"}},
            result(),
        ]
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=responses[len(calls) - 1])

        response = await self.request(handler)
        self.assertEqual(len(calls), 3)
        self.assertEqual(response.raw_text, TEXT)

    async def test_transport_failures_use_existing_retries(self):
        calls = []

        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout("read timed out", request=request)
            if len(calls) == 2:
                return httpx.Response(503, json={"error": "busy"})
            return httpx.Response(200, json=result())

        self.assertEqual((await self.request(handler)).raw_text, TEXT)
        self.assertEqual(len(calls), 3)

    async def test_invalid_or_incomplete_results_are_not_accepted_or_quality_retried(self):
        def handler(body, calls, request):
            calls.append(request)
            return httpx.Response(200, json=body)

        for body in (result(finish_reason="length"), result(index=1), result(text=None),
                     {"pages": []}, {"error": {"message": "OCR did not finish in time"}},
                     {**result(), "usage": {"input_tokens": -1}}):
            calls = []

            with self.subTest(body=body), self.assertRaises(OperationError):
                await self.request(partial(handler, body, calls))
            self.assertEqual(len(calls), 1)

    async def test_authentication_and_balance_errors_keep_existing_vendor_semantics(self):
        for status, error_type in ((401, OCRFatalError), (403, OCRFatalError), (402, OCRBillingError)):
            with self.subTest(status=status), self.assertRaises(error_type):
                await self.request(lambda _request, status=status: httpx.Response(status, json={"error": "account"}))

    async def test_official_response_flows_through_existing_pdf_to_epub_pipeline(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=result())

        craft = AsyncPDFCraft(pdf=PDFOptions(
            ocr=DeepSeekOfficialOCRConfig("key"), ocr_executor=ConcurrentExecutor(FixedCapacity(1)),
        ))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("pdf_craft.pdf.vendor_ocr.httpx.AsyncClient", return_value=client):
            output = Path(self.directory.name) / "book.epub"
            source = Path(__file__).parent / "assets/pdf/friendly.pdf"
            report = await craft.convert_pdf_to_epub(source, output, extraction=ExtractionOptions(page_indexes={1}))
        with ZipFile(output) as epub:
            self.assertTrue(any("Official OCR keeps literal ｜." in unescape(epub.read(name).decode())
                                for name in epub.namelist() if name.endswith((".html", ".xhtml"))))
        self.assertEqual(len(calls), 1)
        self.assertEqual((report.input_tokens, report.output_tokens), (12, 34))
