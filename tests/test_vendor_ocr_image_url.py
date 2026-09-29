# pylint: disable=cell-var-from-loop,protected-access
import asyncio
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from doc_page_extractor.extraction_context import AbortError

from pdf_craft import (
    AsyncPDFCraft,
    ConcurrentExecutor,
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRVendorConfig,
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


class VendorOCRImageURLTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_resolver_reaches_the_vendor_engine(self):
        def resolve(_path: Path) -> str:
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

    async def test_sync_and_async_resolvers_use_the_expected_execution_context(self):
        event_loop_thread = threading.get_ident()
        resolver_threads: list[int] = []
        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured_urls.append(
                payload["messages"][0]["content"][0]["image_url"]["url"],
            )
            return _deepseek_response(request)

        def sync_resolver(_path: Path) -> str:
            resolver_threads.append(threading.get_ident())
            return "https://images.invalid/sync.png"

        async def async_resolver(_path: Path) -> str:
            resolver_threads.append(threading.get_ident())
            await asyncio.sleep(0)
            return "https://images.invalid/async.png"

        for resolver in (sync_resolver, async_resolver):
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

        self.assertNotEqual(resolver_threads[0], event_loop_thread)
        self.assertEqual(resolver_threads[1], event_loop_thread)
        self.assertEqual(captured_urls, [
            "https://images.invalid/sync.png",
            "https://images.invalid/async.png",
        ])

    async def test_unlimited_selects_file_data_or_file_url(self):
        cases = (
            (None, "file_data", base64.b64encode(b"image").decode("ascii")),
            (
                lambda _path: "https://images.invalid/page.png",
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

        def resolver(_path: Path) -> str:
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
            (lambda _path: "", "non-empty URL string", None),
            (lambda _path: "ftp://secret.invalid/image", "HTTP", None),
            (
                lambda _path: "data:image/png;base64,%%%",
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

        def failed_resolver(_path: Path) -> str:
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

        def resolver(_path: Path) -> str:
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

        def interrupted_resolver(_path: Path) -> str:
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
