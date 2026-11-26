# pylint: disable=cell-var-from-loop,protected-access
import asyncio
import base64
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch
from zipfile import ZipFile

import httpx
from doc_page_extractor.extraction_context import AbortError

from pdf_craft import (
    AsyncPDFCraft,
    ConcurrentExecutor,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRVendorConfig,
    ExtractionOptions,
    FixedCapacity,
    OCRImageURLResolver,
    OperationError,
    PDFOptions,
    UnlimitedOCRVendorConfig,
    create_vendor_ocr_request,
)
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime


def _deepseek_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, request=request, json={
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    })


def _resolver_returning(value: str) -> OCRImageURLResolver:
    async def resolve(_path: Path) -> str:
        return value

    return resolve


class VendorOCRImageURLTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_deepseek_multipart_to_epub(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            content_type = request.headers["Content-Type"]
            self.assertTrue(content_type.startswith("multipart/form-data;"))
            multipart = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\n\r\n".encode() + request.content,
            )
            parts = {part.get_param("name", header="content-disposition"): part
                     for part in multipart.iter_parts()}
            self.assertEqual(set(parts), {"purpose", "file"})
            self.assertEqual(parts["purpose"].get_content(), "rich_layout_recognition")
            self.assertEqual(parts["file"].get_content_type(), "image/png")
            self.assertTrue(parts["file"].get_content().startswith(b"\x89PNG\r\n\x1a\n"))
            response = json.dumps({
                "pages": [{
                    "index": 0,
                    "text": "<｜ref｜>text<｜/ref｜><｜box｜>[[100,100,900,200]]<｜/box｜>"
                            "A page recognized by DeepSeek.",
                    "finish_reason": "stop",
                }],
                "usage": {"input_tokens": 12, "output_tokens": 34},
            })
            return httpx.Response(200, request=request, text="\n\n" + response)

        craft = AsyncPDFCraft(pdf=PDFOptions(
            ocr=DeepSeekOCRVendorConfig.official("test-key"),
            ocr_executor=ConcurrentExecutor(FixedCapacity(1)),
        ))
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient", return_value=client,
        ):
            output = Path(directory) / "book.epub"
            source = Path(__file__).parent / "assets/pdf/friendly.pdf"
            result = await craft.convert_pdf_to_epub(
                source, output, extraction=ExtractionOptions(page_indexes={1}),
            )
            with ZipFile(output) as epub:
                self.assertTrue(any(
                    b"A page recognized by DeepSeek." in epub.read(name)
                    for name in epub.namelist() if name.endswith((".html", ".xhtml"))
                ))
        self.assertEqual(calls, [httpx.URL("https://api.deepseek.com/v1/tools/ocr")])
        self.assertEqual((result.input_tokens, result.output_tokens), (12, 34))

    async def test_official_deepseek_reports_invalid_page_details(self):
        cases = (
            ({"error": {"message": "OCR did not finish in time"}}, "did not finish in time"),
            ({
                "id": "ocr-truncated",
                "pages": [{"index": 0, "text": "partial", "finish_reason": "length"}],
                "usage": {"input_tokens": 256, "output_tokens": 4096},
            }, r"truncated the page \(finish_reason=length\): "
               "pdf_page=1, http_status=200, ocr_id='ocr-truncated', "
               "input_tokens=256, output_tokens=4096, image_bytes=1, text_chars=7"),
            ({"pages": [{"index": 1, "text": "complete", "finish_reason": "stop"}]},
             "invalid page index"),
            ({"pages": [{"index": 0, "text": "unconfirmed"}]},
             "invalid page finish reason"),
        )
        for body, message in cases:
            with self.subTest(message=message):
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    return httpx.Response(200, request=request, json=body)

                client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                with patch("pdf_craft.pdf.vendor_ocr.httpx.AsyncClient", return_value=client):
                    async with create_vendor_ocr_request(
                        DeepSeekOCRVendorConfig.official("test-key"),
                        ConcurrentExecutor(FixedCapacity(1)),
                        _resolver_returning("data:image/png;base64,YQ=="),
                    ) as request:
                        with self.assertRaisesRegex(OperationError, message):
                            await request(VendorOCRInput(1, Path("page.png"), lambda: False))
                self.assertEqual(calls, 1)

    async def test_official_deepseek_retries_temporary_errors_in_success_status(self):
        responses = iter([
            {"error": {"message": "The OCR service is temporarily unavailable. Please try again later."}},
            {"error": {"message": "Internal Server Error"}},
            {"pages": [{"index": 0, "text": "", "finish_reason": "stop"}]},
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json=next(responses))

        config = replace(DeepSeekOCRVendorConfig.official("test-key"), retry_interval_seconds=0)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("pdf_craft.pdf.vendor_ocr.httpx.AsyncClient", return_value=client):
            async with create_vendor_ocr_request(
                config,
                ConcurrentExecutor(FixedCapacity(1)),
                _resolver_returning("data:image/png;base64,YQ=="),
            ) as request:
                result = await request(VendorOCRInput(1, Path("page.png"), lambda: False))
        self.assertEqual(result.raw_text, "")

    async def test_public_resolver_reaches_the_vendor_engine(self):
        async def resolve(_path: Path) -> str:
            return "https://images.invalid/page.png"

        resolver: OCRImageURLResolver = resolve
        craft = AsyncPDFCraft(pdf=PDFOptions(
            ocr=DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ocr_executor=ConcurrentExecutor(FixedCapacity(1)),
            ocr_image_url_resolver=resolver,
        ))

        self.assertIs(
            craft._pdf_engine()._ocr_image_url_resolver,
            resolver,
        )

    async def test_default_data_url_is_used_by_both_deepseek_backends(self):
        configs = (
            DeepSeekOCRVendorConfig("https://example.invalid/v1", "key", "v1"),
            DeepSeekOCR2VendorConfig("https://example.invalid/v1", "key", "v2"),
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            image_path.write_bytes(b"rendered image")
            for config in configs:
                with self.subTest(config=type(config).__name__):
                    payloads: list[dict] = []

                    def handler(request: httpx.Request) -> httpx.Response:
                        payloads.append(json.loads(request.content))
                        return _deepseek_response(request)

                    runtime = VendorOCRRuntime(
                        config,
                        ConcurrentExecutor(FixedCapacity(1)),
                    )
                    runtime._client = httpx.AsyncClient(
                        transport=httpx.MockTransport(handler),
                    )
                    await runtime.request(VendorOCRInput(
                        1, image_path, lambda: False,
                    ))
                    await runtime._client.aclose()

                    image_url = payloads[0]["messages"][0]["content"][0][
                        "image_url"
                    ]["url"]
                    self.assertTrue(image_url.startswith("data:image/png;base64,"))
                    self.assertEqual(
                        base64.b64decode(image_url.partition(",")[2]),
                        b"rendered image",
                    )

    async def test_resolver_runs_on_the_event_loop(self):
        event_loop_thread = threading.get_ident()
        resolver_threads: list[int] = []
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured_urls.append(
                payload["messages"][0]["content"][0]["image_url"]["url"],
            )
            return _deepseek_response(request)

        async def resolver(_path: Path) -> str:
            resolver_threads.append(threading.get_ident())
            await asyncio.sleep(0)
            return "https://images.invalid/async.png"

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            resolver,
        )
        runtime._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        await runtime.request(VendorOCRInput(
            1, Path("unused.png"), lambda: False,
        ))
        await runtime._client.aclose()

        self.assertEqual(resolver_threads, [event_loop_thread])
        self.assertEqual(captured_urls, ["https://images.invalid/async.png"])

    async def test_unlimited_selects_file_data_or_file_url(self):
        cases = (
            (None, "file_data", base64.b64encode(b"image").decode("ascii")),
            (
                _resolver_returning("https://images.invalid/page.png"),
                "file_url",
                "https://images.invalid/page.png",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            image_path.write_bytes(b"image")
            for resolver, expected_field, expected_value in cases:
                with self.subTest(expected_field=expected_field):
                    forms: list[dict[str, list[str]]] = []

                    def handler(request: httpx.Request) -> httpx.Response:
                        forms.append(parse_qs(request.content.decode()))
                        return httpx.Response(200, request=request, json={
                            "result": {"task_id": "task-1"},
                        })

                    runtime = VendorOCRRuntime(
                        UnlimitedOCRVendorConfig("ak", "sk"),
                        ConcurrentExecutor(FixedCapacity(1)),
                        resolver,
                    )
                    runtime._client = httpx.AsyncClient(
                        transport=httpx.MockTransport(handler),
                    )
                    runtime._access_token = "token"
                    resolved = await runtime._resolve_image_url(VendorOCRInput(
                        1, image_path, lambda: False,
                    ))
                    await runtime._submit_unlimited(resolved, None)
                    await runtime._client.aclose()

                    self.assertEqual(forms[0][expected_field], [expected_value])
                    self.assertEqual(forms[0]["file_name"], ["page.png"])
                    absent_field = (
                        "file_url" if expected_field == "file_data" else "file_data"
                    )
                    self.assertNotIn(absent_field, forms[0])

    async def test_resolved_url_is_reused_across_transport_retry(self):
        requests = 0
        resolutions = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                return httpx.Response(429, request=request)
            return _deepseek_response(request)

        async def resolver(_path: Path) -> str:
            nonlocal resolutions
            resolutions += 1
            return "https://images.invalid/page.png"

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
                retry_times=1, retry_interval_seconds=0,
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            resolver,
        )
        runtime._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        await runtime.request(VendorOCRInput(
            1, Path("unused.png"), lambda: False,
        ))
        await runtime._client.aclose()

        self.assertEqual(requests, 2)
        self.assertEqual(resolutions, 1)

    async def test_invalid_results_and_resolver_errors_are_safe_and_actionable(self):
        cases = (
            (_resolver_returning(""), "non-empty URL string", None),
            (_resolver_returning("ftp://secret.invalid/image"), "HTTP", None),
            (
                _resolver_returning("data:image/png;base64,%%%"),
                "non-empty Base64 image data",
                None,
            ),
        )
        for resolver, expected, _ in cases:
            with self.subTest(expected=expected):
                runtime = VendorOCRRuntime(
                    DeepSeekOCRVendorConfig(
                        "https://example.invalid/v1", "key", "model",
                    ),
                    ConcurrentExecutor(FixedCapacity(1)),
                    resolver,
                )
                with self.assertRaisesRegex(OperationError, expected):
                    await runtime.request(VendorOCRInput(
                        1, Path("unused.png"), lambda: False,
                    ))

        secret = RuntimeError("https://signed.invalid/private-token")

        async def failed_resolver(_path: Path) -> str:
            raise secret

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            failed_resolver,
        )
        with self.assertRaises(OperationError) as raised:
            await runtime.request(VendorOCRInput(
                1, Path("unused.png"), lambda: False,
            ))
        self.assertNotIn("private-token", str(raised.exception))
        self.assertIs(raised.exception.__cause__, secret)

    async def test_cancellation_propagates_from_async_resolver(self):
        started = asyncio.Event()
        never = asyncio.Event()

        async def resolver(_path: Path) -> str:
            started.set()
            await never.wait()
            return "https://images.invalid/page.png"

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            resolver,
        )
        pending = asyncio.create_task(runtime.request(VendorOCRInput(
            1, Path("unused.png"), lambda: False,
        )))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    async def test_extraction_abort_is_not_wrapped_during_resolution(self):
        resolver_called = False

        async def resolver(_path: Path) -> str:
            nonlocal resolver_called
            resolver_called = True
            return "https://images.invalid/page.png"

        async with create_vendor_ocr_request(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            resolver,
        ) as request:
            with self.assertRaises(AbortError):
                await request(VendorOCRInput(
                    1, Path("unused.png"), lambda: True,
                ))
        self.assertFalse(resolver_called)

        async def interrupted_resolver(_path: Path) -> str:
            raise AbortError()

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(1)),
            interrupted_resolver,
        )
        with self.assertRaises(AbortError):
            await runtime.request(VendorOCRInput(
                1, Path("unused.png"), lambda: False,
            ))

    async def test_request_iterable_stays_lazy_while_resolution_is_blocked(self):
        consumed = 0
        resolver_started = asyncio.Event()
        never = asyncio.Event()

        def requests():
            nonlocal consumed
            for page_index in range(100):
                consumed += 1
                yield VendorOCRInput(
                    page_index, Path("unused.png"), lambda: False,
                )

        async def resolver(_path: Path) -> str:
            resolver_started.set()
            await never.wait()
            return "https://images.invalid/page.png"

        runtime = VendorOCRRuntime(
            DeepSeekOCRVendorConfig(
                "https://example.invalid/v1", "key", "model",
            ),
            ConcurrentExecutor(FixedCapacity(2)),
            resolver,
        )

        async def consume() -> None:
            async for _ in runtime.request_many(requests()):
                pass

        pending = asyncio.create_task(consume())
        await resolver_started.wait()
        self.assertEqual(consumed, 1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending


if __name__ == "__main__":
    unittest.main()
