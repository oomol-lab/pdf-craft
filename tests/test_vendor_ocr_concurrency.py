# pylint: disable=cell-var-from-loop,protected-access
import asyncio
import tempfile
import threading
import time
import unittest
from contextlib import aclosing
from pathlib import Path
from typing import Any, AsyncGenerator, cast
from urllib.parse import parse_qs
from unittest.mock import patch

import httpx
from PIL import Image

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity,
    NonContinuableError, OperationError, OperationResult, RateLimitedError,
    UnlimitedOCRVendorConfig, create_vendor_ocr_request,
)
from pdf_craft.error import (
    NoUsableOCRPagesError, OCRBillingError, OCRError, OCRFatalError, PDFError,
)
from pdf_craft.pdf.ocr import OCR, OCREventKind
from pdf_craft.pdf import ocr as ocr_module
from pdf_craft.pdf.vendor_ocr import (
    VendorOCRInput, VendorOCRResponse, VendorOCRResult, VendorOCRRuntime,
)
from pdf_craft.pdf.types import Page
from pdf_craft.transform import PDFExtractionEngine
from doc_page_extractor.extraction_context import AbortError, TokenLimitError
from doc_page_extractor.errors import VendorOCRRequestError


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
    async def test_vendor_engine_requires_explicit_executor(self):
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
        )
        with self.assertRaisesRegex(ValueError, "explicit OCR executor"):
            PDFExtractionEngine(ocr=config)

    async def test_bound_vendor_request_retries_429_as_separate_operations(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, request=request, json={"error": "slow"})
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": ""}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
            retry_times=1,
            retry_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            with patch(
                "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                return_value=client,
            ):
                async with create_vendor_ocr_request(
                    config, ConcurrentExecutor(FixedCapacity(1)),
                ) as request:
                    response = await request(VendorOCRInput(
                        1, image_path, lambda: False,
                    ))

        self.assertEqual(calls, 2)
        self.assertEqual((response.input_tokens, response.output_tokens), (2, 3))

    async def test_batch_retry_is_not_blocked_by_slow_initial_page(self):
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
            retry_times=1,
            retry_interval_seconds=0,
        )
        retried = asyncio.Event()
        first_calls = 0

        async def request(_runtime, vendor_request):
            nonlocal first_calls
            if vendor_request.page_index == 1:
                first_calls += 1
                if first_calls == 1:
                    raise RateLimitedError(retry_after=0)
                retried.set()
            else:
                await asyncio.wait_for(retried.wait(), 1)
            return VendorOCRResponse({}, raw_text="")

        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for page_index in (1, 2):
                path = Path(directory) / f"page-{page_index}.png"
                Image.new("RGB", (4, 4), "white").save(path)
                paths.append(path)
            with patch.object(VendorOCRRuntime, "_request_deepseek", new=request):
                async with VendorOCRRuntime(
                    config, ConcurrentExecutor(FixedCapacity(2)),
                ) as runtime:
                    results = [
                        result
                        async for result in runtime.request_many(
                            VendorOCRInput(index, path, lambda: False)
                            for index, path in enumerate(paths, start=1)
                        )
                    ]

        self.assertEqual({result.request.page_index for result in results}, {1, 2})
        self.assertTrue(all(result.succeeded for result in results))
        self.assertEqual(first_calls, 2)

    async def test_batch_retry_window_bounds_input_and_pending_tasks(self):
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
            retry_times=1,
            retry_interval_seconds=0,
        )
        consumed = 0
        retries_started = 0
        window_full = asyncio.Event()
        release = asyncio.Event()

        def requests():
            nonlocal consumed
            for page_index in range(1000):
                consumed += 1
                yield VendorOCRInput(
                    page_index, Path("unused.png"), lambda: False,
                )

        async def request(_runtime, _request):
            raise RateLimitedError(retry_after=0)

        async def retry(
            _runtime, _request, _error, *, attempts_used,
        ):
            nonlocal retries_started
            self.assertEqual(attempts_used, 1)
            retries_started += 1
            if retries_started == 16:
                window_full.set()
            await release.wait()
            return VendorOCRResponse({}, raw_text="")

        with patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ), patch.object(
            VendorOCRRuntime, "_retry_request_after_error", new=retry,
        ):
            async with VendorOCRRuntime(
                config, ConcurrentExecutor(FixedCapacity(2)),
            ) as runtime:
                async def consume() -> None:
                    async for _ in runtime.request_many(requests()):
                        pass

                pending = asyncio.create_task(consume())
                await asyncio.wait_for(window_full.wait(), 1)
                await asyncio.sleep(0)
                self.assertEqual(retries_started, 16)
                # Retry window + two active leases + map's one-result handoff.
                self.assertLessEqual(consumed, 19)
                self.assertFalse(pending.done())
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending

    async def test_deepseek_retry_fatal_precedes_cancelled_sibling(self):
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1", api_key="key", model="model",
            retry_times=1, retry_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for page_index in (1, 2):
                path = Path(directory) / f"page-{page_index}.png"
                Image.new("RGB", (4, 4), "white").save(path)
                paths.append(path)

            for _ in range(25):
                calls: dict[int, int] = {1: 0, 2: 0}
                retries_started = asyncio.Event()
                never = asyncio.Event()

                async def request(_runtime, vendor_request):
                    page_index = vendor_request.page_index
                    calls[page_index] += 1
                    if calls[page_index] == 1:
                        raise RateLimitedError(retry_after=0)
                    if sum(count > 1 for count in calls.values()) == 2:
                        retries_started.set()
                    await retries_started.wait()
                    if page_index == 1:
                        raise NonContinuableError("quota exhausted")
                    await never.wait()
                    raise AssertionError("cancelled retry continued")

                with patch.object(
                    VendorOCRRuntime, "_request_deepseek", new=request,
                ):
                    async with VendorOCRRuntime(
                        config, ConcurrentExecutor(FixedCapacity(2)),
                    ) as runtime:
                        with self.assertRaisesRegex(
                            NonContinuableError, "quota exhausted",
                        ):
                            async for _result in runtime.request_many(
                                VendorOCRInput(index, path, lambda: False)
                                for index, path in enumerate(paths, start=1)
                            ):
                                pass

    async def test_unlimited_fatal_precedes_cancelled_submit_sibling(self):
        config = UnlimitedOCRVendorConfig(
            ak="ak", sk="sk", retry_times=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for page_index in (1, 2):
                path = Path(directory) / f"page-{page_index}.png"
                Image.new("RGB", (4, 4), "white").save(path)
                paths.append(path)

            for _ in range(25):
                submits_started = asyncio.Event()
                started = 0
                never = asyncio.Event()

                async def post_form(
                    _runtime, _url, data, page_index, action, *, result_kind,
                ):
                    nonlocal started
                    del page_index
                    self.assertEqual(action, "Unlimited OCR submit")
                    self.assertEqual(result_kind, "submit")
                    started += 1
                    if started == 2:
                        submits_started.set()
                    await submits_started.wait()
                    if data["file_name"] == "page-1.png":
                        raise OCRBillingError(1)
                    await never.wait()
                    raise AssertionError("cancelled submit continued")

                with patch.object(
                    VendorOCRRuntime, "_post_form", new=post_form,
                ):
                    async with VendorOCRRuntime(
                        config, ConcurrentExecutor(FixedCapacity(2)),
                    ) as runtime:
                        runtime._access_token = "token"
                        with self.assertRaises(OCRBillingError):
                            async for _result in runtime.request_many(
                                VendorOCRInput(index, path, lambda: False)
                                for index, path in enumerate(paths, start=1)
                            ):
                                pass

    async def test_vendor_payment_error_closes_shared_executor(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, request=request, json={"error": "balance"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(1))
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
            retry_times=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            with patch(
                "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                return_value=client,
            ):
                async with create_vendor_ocr_request(config, executor) as request:
                    with self.assertRaises(OCRBillingError):
                        await request(VendorOCRInput(7, image_path, lambda: False))
                    with self.assertRaises(OCRBillingError):
                        await executor.run(lambda: asyncio.sleep(0))

    async def test_429_insufficient_quota_is_terminal_and_preserves_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, request=request, json={
                "error": {"type": "insufficient_quota", "code": None},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(1))
        config = DeepSeekOCRVendorConfig(
            base_url="https://example.invalid/v1",
            api_key="key",
            model="model",
            retry_times=3,
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            with patch(
                "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                return_value=client,
            ):
                async with create_vendor_ocr_request(config, executor) as request:
                    with self.assertRaises(OCRBillingError) as raised:
                        await request(VendorOCRInput(
                            9, image_path, lambda: False,
                        ))

        envelope = raised.exception.__cause__
        self.assertIsInstance(envelope, VendorOCRRequestError)
        assert isinstance(envelope, VendorOCRRequestError)
        raw = envelope.__cause__
        self.assertIsInstance(raw, httpx.HTTPStatusError)
        assert isinstance(raw, httpx.HTTPStatusError)
        self.assertEqual(raw.response.status_code, 429)

    async def test_unlimited_query_retry_does_not_resubmit_task(self):
        calls: list[str] = []
        query_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal query_count
            path = request.url.path
            calls.append(path)
            if path.endswith("/oauth/2.0/token"):
                return httpx.Response(
                    200, request=request, json={"access_token": "token"},
                )
            if path.endswith("/task/query"):
                query_count += 1
                if query_count == 1:
                    return httpx.Response(
                        500, request=request, json={"error": "temporary"},
                    )
                return httpx.Response(200, request=request, json={
                    "result": {
                        "status": "success",
                        "parse_result_url": "https://download.invalid/result",
                    },
                })
            if path.endswith("/task"):
                return httpx.Response(
                    200, request=request, json={"result": {"task_id": "task-1"}},
                )
            return httpx.Response(200, request=request, json={"pages": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        config = UnlimitedOCRVendorConfig(
            ak="ak", sk="sk", base_url="https://baidu.invalid",
            retry_times=1, retry_interval_seconds=0,
            poll_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            with patch(
                "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                return_value=client,
            ):
                async with create_vendor_ocr_request(
                    config, ConcurrentExecutor(FixedCapacity(1)),
                ) as request:
                    response = await request(VendorOCRInput(
                        1, image_path, lambda: False,
                    ))

        self.assertEqual(response.data["task_id"], "task-1")
        self.assertEqual(sum(path.endswith("/task") for path in calls), 1)
        self.assertEqual(sum(path.endswith("/task/query") for path in calls), 2)

    async def test_unlimited_expired_token_is_refreshed_for_current_operation(self):
        token_calls = 0
        submit_tokens: list[str] = []
        query_tokens: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_calls
            path = request.url.path
            if path.endswith("/oauth/2.0/token"):
                token_calls += 1
                return httpx.Response(200, request=request, json={
                    "access_token": f"token-{token_calls}",
                })
            if path.endswith("/task/query"):
                token = request.url.params["access_token"]
                query_tokens.append(token)
                token_number = int(token.removeprefix("token-"))
                if token_number <= 3:
                    return httpx.Response(200, request=request, json={
                        "error_code": (100, 110, 111)[token_number - 1],
                        "error_msg": "expired",
                    })
                return httpx.Response(200, request=request, json={
                    "result": {
                        "status": "success",
                        "parse_result_url": "https://download.invalid/result",
                    },
                })
            if path.endswith("/task"):
                token = request.url.params["access_token"]
                submit_tokens.append(token)
                return httpx.Response(200, request=request, json={
                    "result": {"task_id": "task-1"},
                })
            return httpx.Response(200, request=request, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(1))
        config = UnlimitedOCRVendorConfig(
            ak="ak", sk="sk", retry_times=3, retry_interval_seconds=0,
            poll_interval_seconds=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            with patch(
                "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                return_value=client,
            ):
                async with create_vendor_ocr_request(config, executor) as request:
                    response = await request(VendorOCRInput(
                        1, image_path, lambda: False,
                    ))

        self.assertEqual(response.data["task_id"], "task-1")
        self.assertEqual(token_calls, 4)
        self.assertEqual(submit_tokens, ["token-1"])
        self.assertEqual(query_tokens, [
            "token-1", "token-2", "token-3", "token-4",
        ])
        self.assertEqual(
            await executor.run(lambda: asyncio.sleep(0, result="open")), "open",
        )

    async def test_unlimited_task_failure_resubmits_page(self):
        submit_count = 0
        query_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal submit_count, query_count
            path = request.url.path
            if path.endswith("/task/query"):
                query_count += 1
                task_id = parse_qs(request.content.decode())["task_id"][0]
                if task_id == "task-1":
                    return httpx.Response(200, request=request, json={
                        "error_code": 282000, "error_msg": "resubmit",
                    })
                if task_id == "task-2":
                    return httpx.Response(200, request=request, json={
                        "result": {
                            "task_id": task_id,
                            "status": "failed",
                            "task_error": "任务失败",
                        },
                    })
                return httpx.Response(200, request=request, json={
                    "result": {
                        "status": "success",
                        "parse_result_url": "https://download.invalid/result",
                    },
                })
            if path.endswith("/task"):
                submit_count += 1
                return httpx.Response(200, request=request, json={
                    "result": {"task_id": f"task-{submit_count}"},
                })
            return httpx.Response(200, request=request, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(1))
        runtime = VendorOCRRuntime(
            UnlimitedOCRVendorConfig(
                ak="ak", sk="sk", retry_times=2, retry_interval_seconds=0,
                poll_interval_seconds=0,
            ),
            executor,
        )
        runtime._client = client
        runtime._access_token = "token"
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (4, 4), "white").save(image_path)
            response = await runtime.request(VendorOCRInput(
                1, image_path, lambda: False,
            ))

        self.assertEqual(response.data["task_id"], "task-3")
        self.assertEqual(submit_count, 3)
        self.assertEqual(query_count, 3)
        self.assertEqual(
            await executor.run(lambda: asyncio.sleep(0, result="open")), "open",
        )
        await client.aclose()

    async def test_unlimited_page_window_consumes_requests_lazily(self):
        config = UnlimitedOCRVendorConfig(
            ak="ak", sk="sk", page_window=3,
        )
        consumed = 0
        started = 0
        window_started = asyncio.Event()
        release = asyncio.Event()

        def requests():
            nonlocal consumed
            for page_index in range(1, 1001):
                consumed += 1
                yield VendorOCRInput(
                    page_index, Path(f"page-{page_index}.png"), lambda: False,
                )

        async def request(_runtime, _request):
            nonlocal started
            started += 1
            if started == config.page_window:
                window_started.set()
            await release.wait()
            return VendorOCRResponse({})

        with patch.object(VendorOCRRuntime, "request", new=request):
            runtime = VendorOCRRuntime(
                config, ConcurrentExecutor(FixedCapacity(2)),
            )
            results = cast(
                AsyncGenerator[VendorOCRResult, None],
                runtime.request_many(requests()),
            )
            async with aclosing(results):
                first = asyncio.create_task(anext(results))
                await asyncio.wait_for(window_started.wait(), 1)
                self.assertEqual(consumed, config.page_window)
                self.assertEqual(started, config.page_window)
                release.set()
                self.assertTrue((await first).succeeded)

        self.assertLess(consumed, 1000)

    async def test_unlimited_retryable_application_codes_reacquire_capacity(self):
        for error_code in (1, 2, 4, 18):
            with self.subTest(error_code=error_code):
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return httpx.Response(200, request=request, json={
                            "error_code": error_code,
                            "error_msg": "retry the request",
                        })
                    return httpx.Response(200, request=request, json={
                        "result": {"status": "running"},
                    })

                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler),
                )
                executor = ConcurrentExecutor(FixedCapacity(1))
                runtime = VendorOCRRuntime(
                    UnlimitedOCRVendorConfig(
                        ak="ak", sk="sk", retry_times=1,
                        retry_interval_seconds=0,
                    ),
                    executor,
                )
                runtime._client = client
                result = await runtime._run_io_with_retry(
                    lambda: runtime._post_form(
                        "https://example.invalid/task/query",
                        {"task_id": "task"}, 1, "Unlimited OCR query",
                        result_kind="query",
                    )
                )

                self.assertEqual(result["result"]["status"], "running")
                self.assertEqual(calls, 2)
                self.assertEqual(
                    await executor.run(lambda: asyncio.sleep(0, result="open")),
                    "open",
                )
                await client.aclose()

    async def test_unlimited_application_errors_use_three_error_flows(self):
        responses = iter((18, 17, 282006, 216201))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json={
                "error_code": next(responses), "error_msg": "provider error",
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        config = UnlimitedOCRVendorConfig(
            ak="ak", sk="sk", retry_times=0,
        )
        runtime = VendorOCRRuntime(
            config, ConcurrentExecutor(FixedCapacity(1)),
        )
        runtime._client = client
        error_types = (
            RateLimitedError,
            OCRBillingError,
            NonContinuableError,
            OperationError,
        )
        for error_type in error_types:
            with self.assertRaises(error_type) as raised:
                await runtime._post_form(
                    "https://example.invalid", {}, 3, "Unlimited OCR query",
                    result_kind="query",
                )
            envelope = raised.exception.__cause__
            self.assertIsInstance(envelope, VendorOCRRequestError)
        await client.aclose()

    async def test_unlimited_quota_and_permission_errors_bypass_fallback(self):
        cases = (
            (282005, OCRBillingError),
            (282006, OCRFatalError),
        )
        for error_code, error_type in cases:
            with self.subTest(error_code=error_code):
                def handler(request: httpx.Request) -> httpx.Response:
                    if request.url.path.endswith("/oauth/2.0/token"):
                        return httpx.Response(200, request=request, json={
                            "access_token": "token",
                        })
                    return httpx.Response(200, request=request, json={
                        "error_code": error_code, "error_msg": "terminal",
                    })

                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler),
                )
                executor = ConcurrentExecutor(FixedCapacity(2))
                ignored: list[OCRError] = []
                ocr = OCR(
                    UnlimitedOCRVendorConfig(
                        ak="ak", sk="sk", retry_times=0,
                    ),
                    cast(Any, _Handler([])),
                )
                with tempfile.TemporaryDirectory() as directory, patch(
                    "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
                    return_value=client,
                ):
                    root = Path(directory)
                    with self.assertRaises(error_type):
                        async for _ in ocr.recognize_vendor(
                            executor,
                            pdf_path=root / "source.pdf",
                            asset_path=root / "assets",
                            ocr_path=root / "ocr",
                            ignore_ocr_errors=(
                                lambda error: ignored.append(error) or True
                            ),
                        ):
                            pass

                self.assertEqual(ignored, [])
                with self.assertRaises(error_type):
                    await executor.run(lambda: asyncio.sleep(0))

    async def test_unlimited_failed_quota_task_bypasses_fallback(self):
        query_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal query_calls
            path = request.url.path
            if path.endswith("/oauth/2.0/token"):
                return httpx.Response(200, request=request, json={
                    "access_token": "token",
                })
            if path.endswith("/task/query"):
                query_calls += 1
                return httpx.Response(200, request=request, json={
                    "error_code": 0,
                    "result": {
                        "task_id": "task-1",
                        "status": "failed",
                        "task_error": "额度不够",
                    },
                })
            return httpx.Response(200, request=request, json={
                "result": {"task_id": "task-1"},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(2))
        ignored: list[OCRError] = []
        ocr = OCR(
            UnlimitedOCRVendorConfig(
                ak="ak", sk="sk", retry_times=0, poll_interval_seconds=0,
            ),
            cast(Any, _Handler([])),
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
            return_value=client,
        ):
            root = Path(directory)
            with self.assertRaises(OCRBillingError) as raised:
                async for _ in ocr.recognize_vendor(
                    executor,
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    ignore_ocr_errors=lambda error: ignored.append(error) or True,
                ):
                    pass

            self.assertEqual(ignored, [])
            self.assertEqual(list((root / "ocr").glob("page_*.failed")), [])

        self.assertGreaterEqual(query_calls, 1)
        envelope = raised.exception.__cause__
        self.assertIsInstance(envelope, VendorOCRRequestError)
        assert isinstance(envelope, VendorOCRRequestError)
        raw = envelope.__cause__
        self.assertIsInstance(raw, httpx.HTTPStatusError)
        assert isinstance(raw, httpx.HTTPStatusError)
        self.assertEqual(raw.response.status_code, 200)
        self.assertEqual(
            raw.response.json()["result"]["task_error"], "额度不够",
        )
        with self.assertRaises(OCRBillingError):
            await executor.run(lambda: asyncio.sleep(0))

    async def test_vendor_malformed_success_bodies_preserve_response(self):
        unlimited_cases = (
            ("top-level", ["malformed"]),
            ("empty-submit", {"result": {}}),
            ("empty-query", {"result": {}}),
            ("unknown-query", {"result": {"status": "unknown"}}),
        )
        for case, malformed in unlimited_cases:
            with self.subTest(case=case):
                calls = 0

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if case in {"empty-query", "unknown-query"} and (
                        request.url.path.endswith("/task")
                    ):
                        return httpx.Response(200, request=request, json={
                            "result": {"task_id": "task"},
                        })
                    return httpx.Response(200, request=request, json=malformed)

                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(handler),
                )
                runtime = VendorOCRRuntime(
                    UnlimitedOCRVendorConfig(
                        ak="ak", sk="sk", retry_times=0,
                        poll_interval_seconds=0,
                    ),
                    ConcurrentExecutor(FixedCapacity(1)),
                )
                runtime._client = client
                runtime._access_token = "token"
                with tempfile.TemporaryDirectory() as directory:
                    image_path = Path(directory) / "page.png"
                    Image.new("RGB", (4, 4), "white").save(image_path)
                    results = [
                        result async for result in runtime.request_many([
                            VendorOCRInput(1, image_path, lambda: False),
                        ])
                    ]

                self.assertEqual(len(results), 1)
                error = results[0].error
                self.assertIsInstance(error, OperationError)
                assert isinstance(error, OperationError)
                envelope = error.__cause__
                self.assertIsInstance(envelope, VendorOCRRequestError)
                assert isinstance(envelope, VendorOCRRequestError)
                raw = envelope.__cause__
                self.assertIsInstance(raw, httpx.HTTPStatusError)
                assert isinstance(raw, httpx.HTTPStatusError)
                self.assertEqual(raw.response.status_code, 200)
                self.assertEqual(raw.response.json(), malformed)
                self.assertEqual(
                    calls,
                    2 if case in {"empty-query", "unknown-query"} else 1,
                )
                await client.aclose()

        deepseek_cases = (
            {
                "usage": ["malformed"],
                "choices": [{"message": {"content": ""}}],
            },
            {"usage": {}, "choices": {"malformed": True}},
        )
        for malformed in deepseek_cases:
            with self.subTest(deepseek=malformed):
                def deepseek_handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, request=request, json=malformed)

                client = httpx.AsyncClient(
                    transport=httpx.MockTransport(deepseek_handler),
                )
                runtime = VendorOCRRuntime(
                    DeepSeekOCRVendorConfig(
                        base_url="https://example.invalid/v1",
                        api_key="key",
                        model="model",
                        retry_times=0,
                    ),
                    ConcurrentExecutor(FixedCapacity(1)),
                )
                runtime._client = client
                with tempfile.TemporaryDirectory() as directory:
                    image_path = Path(directory) / "page.png"
                    Image.new("RGB", (4, 4), "white").save(image_path)
                    results = [
                        result async for result in runtime.request_many([
                            VendorOCRInput(1, image_path, lambda: False),
                        ])
                    ]

                error = results[0].error
                self.assertIsInstance(error, OperationError)
                assert isinstance(error, OperationError)
                envelope = error.__cause__
                self.assertIsInstance(envelope, VendorOCRRequestError)
                assert isinstance(envelope, VendorOCRRequestError)
                raw = envelope.__cause__
                self.assertIsInstance(raw, httpx.HTTPStatusError)
                assert isinstance(raw, httpx.HTTPStatusError)
                self.assertEqual(raw.response.status_code, 200)
                self.assertEqual(raw.response.json(), malformed)
                await client.aclose()

    async def test_unlimited_oauth_error_is_fatal_and_bypasses_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(request.url.path.endswith("/oauth/2.0/token"))
            return httpx.Response(200, request=request, json={
                "error": "invalid_client",
                "error_description": "unknown client id",
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = ConcurrentExecutor(FixedCapacity(2))
        ignored: list[OCRError] = []
        rendered: list[int] = []
        ocr = OCR(
            UnlimitedOCRVendorConfig(
                ak="invalid", sk="invalid", retry_times=0,
            ),
            cast(Any, _Handler(rendered)),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
            return_value=client,
        ):
            root = Path(directory)
            with self.assertRaises(OCRFatalError) as raised:
                async for _ in ocr.recognize_vendor(
                    executor,
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    ignore_ocr_errors=lambda error: ignored.append(error) or True,
                ):
                    pass

            self.assertEqual(ignored, [])
            self.assertEqual(list((root / "ocr").glob("page_*.failed")), [])

        envelope = raised.exception.__cause__
        self.assertIsInstance(envelope, VendorOCRRequestError)
        assert isinstance(envelope, VendorOCRRequestError)
        raw = envelope.__cause__
        self.assertIsInstance(raw, httpx.HTTPStatusError)
        assert isinstance(raw, httpx.HTTPStatusError)
        self.assertEqual(raw.response.status_code, 200)
        self.assertEqual(raw.response.json()["error"], "invalid_client")
        with self.assertRaises(OCRFatalError):
            await executor.run(lambda: asyncio.sleep(0))

    async def test_unlimited_malformed_page_response_reaches_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/oauth/2.0/token"):
                return httpx.Response(
                    200, request=request, json={"access_token": "token"},
                )
            if path.endswith("/task/query"):
                task_id = parse_qs(request.content.decode())["task_id"][0]
                return httpx.Response(200, request=request, json={
                    "result": {
                        "status": "success",
                        "parse_result_url": f"https://download.invalid/{task_id}",
                    },
                })
            if path.endswith("/task"):
                file_name = parse_qs(request.content.decode())["file_name"][0]
                if file_name == "page_2.png":
                    return httpx.Response(
                        200, request=request, json={"result": ["malformed"]},
                    )
                return httpx.Response(200, request=request, json={
                    "result": {"task_id": file_name},
                })
            return httpx.Response(200, request=request, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ignored: list[OCRError] = []
        rendered: list[int] = []
        ocr = OCR(
            UnlimitedOCRVendorConfig(
                ak="ak", sk="sk", retry_times=0, poll_interval_seconds=0,
            ),
            cast(Any, _Handler(rendered)),
        )

        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.vendor_ocr.httpx.AsyncClient",
            return_value=client,
        ):
            root = Path(directory)
            events = [
                event
                async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(2)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    ignore_ocr_errors=lambda error: ignored.append(error) or True,
                )
            ]

            self.assertEqual([error.page_index for error in ignored], [2])
            self.assertTrue((root / "ocr/page_2.failed").exists())
            self.assertTrue(all(
                (root / "ocr" / f"page_{page_index}.xml").exists()
                for page_index in (1, 2, 3)
            ))
            page_two = next(
                event for event in events
                if event.page_index == 2 and event.kind == OCREventKind.FAILED
            )
            self.assertIs(page_two.error, ignored[0])

        operation_error = ignored[0].__cause__
        self.assertIsInstance(operation_error, OperationError)
        assert isinstance(operation_error, OperationError)
        envelope = operation_error.__cause__
        self.assertIsInstance(envelope, VendorOCRRequestError)
        assert isinstance(envelope, VendorOCRRequestError)
        raw = envelope.__cause__
        self.assertIsInstance(raw, httpx.HTTPStatusError)
        assert isinstance(raw, httpx.HTTPStatusError)
        self.assertEqual(raw.response.status_code, 200)
        self.assertEqual(raw.response.json()["result"], ["malformed"])

    async def test_cancellation_stops_serial_render_and_cleans_temporary_files(self):
        render_started = threading.Event()
        rendered: list[int] = []

        class SlowDocument(_Document):
            pages_count = 5

            def render_page(self, page_index: int, dpi: int) -> Image.Image:
                del dpi
                rendered.append(page_index)
                render_started.set()
                time.sleep(0.05)
                return Image.new("RGB", (12, 16), "white")

        class SlowHandler:
            def open(self, _pdf_path: Path) -> SlowDocument:
                return SlowDocument(rendered)

        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, SlowHandler()),
        )
        temporary_paths: list[Path] = []

        def tracked_temporary_directory(*args, **kwargs):
            temporary = tempfile.TemporaryDirectory(*args, **kwargs)
            temporary_paths.append(Path(temporary.name))
            return temporary

        engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
        engine.__dict__["_ocr"] = ocr
        engine.__dict__["_ocr_executor"] = ConcurrentExecutor(FixedCapacity(2))

        async def consume(root: Path) -> None:
            await engine.extract_package_async(
                pdf_path=root / "source.pdf",
                analysing_path=root,
                ocr_size="gundam",
                dpi=None,
                max_page_image_file_size=None,
                includes_footnotes=False,
                ignore_pdf_errors=False,
                ignore_ocr_errors=False,
                generate_plot=False,
                includes_cover=False,
                aborted=lambda: False,
                page_indexes=None,
                max_tokens=None,
                max_output_tokens=None,
            )

        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.ocr.TemporaryDirectory",
            side_effect=tracked_temporary_directory,
        ):
            task = asyncio.create_task(consume(Path(directory)))
            self.assertTrue(await asyncio.to_thread(render_started.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(rendered, [1])
        self.assertTrue(temporary_paths)
        self.assertTrue(all(not path.exists() for path in temporary_paths))

    async def test_cancellation_settles_vendor_siblings_before_cleanup(self):
        rendered: list[int] = []
        started_pages: list[int] = []
        cancelled_pages: list[int] = []
        two_started = asyncio.Event()
        never = asyncio.Event()

        async def request(_runtime, request):
            started_pages.append(request.page_index)
            if len(started_pages) == 2:
                two_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled_pages.append(request.page_index)
                raise

        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )
        temporary_paths: list[Path] = []

        def tracked_temporary_directory(*args, **kwargs):
            temporary = tempfile.TemporaryDirectory(*args, **kwargs)
            temporary_paths.append(Path(temporary.name))
            return temporary

        async def consume(root: Path) -> None:
            stream = ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(2)),
                pdf_path=root / "source.pdf",
                asset_path=root / "assets",
                ocr_path=root / "ocr",
            )
            async with aclosing(stream):
                async for _ in stream:
                    pass

        with tempfile.TemporaryDirectory() as directory, patch(
            "pdf_craft.pdf.ocr.TemporaryDirectory",
            side_effect=tracked_temporary_directory,
        ), patch.object(VendorOCRRuntime, "_request_deepseek", new=request):
            task = asyncio.create_task(consume(Path(directory)))
            await asyncio.wait_for(two_started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(set(started_pages), {1, 2})
        self.assertEqual(set(cancelled_pages), {1, 2})
        self.assertTrue(temporary_paths)
        self.assertTrue(all(not path.exists() for path in temporary_paths))

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
        executor = _SequentialExecutor()

        async def request(_runtime, _vendor_request):
            return VendorOCRResponse({}, raw_text="", input_tokens=1, output_tokens=1)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
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
        active = 0
        maximum = 0
        started_after_render: list[bool] = []

        async def request(_runtime, vendor_request):
            nonlocal active, maximum
            started_after_render.append(rendered == [1, 2, 3])
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.02)
                if vendor_request.page_index == 2:
                    raise OCRError("ignored", page_index=2, step_index=1)
                return VendorOCRResponse(
                    {}, raw_text="",
                    input_tokens=vendor_request.page_index,
                    output_tokens=vendor_request.page_index,
                )
            finally:
                active -= 1

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
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
            self.assertTrue(all(started_after_render))
            self.assertEqual(maximum, 2)
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

    async def test_deepseek_footnote_stage_reuses_network_executor_only(self):
        rendered: list[int] = []
        requests: list[tuple[int, str]] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
            ),
            cast(Any, _Handler(rendered)),
        )

        async def request(_runtime, vendor_request):
            requests.append((vendor_request.page_index, vendor_request.image_path.name))
            return VendorOCRResponse({}, raw_text="", input_tokens=1, output_tokens=1)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
            root = Path(directory)
            events = [
                event
                async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(3)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    includes_footnotes=True,
                )
            ]

        self.assertEqual(rendered, [1, 2, 3])
        self.assertEqual([page for page, _ in requests].count(1), 2)
        self.assertEqual([page for page, _ in requests].count(2), 2)
        self.assertEqual([page for page, _ in requests].count(3), 2)
        self.assertEqual(
            len([event for event in events if event.kind == OCREventKind.COMPLETE]),
            3,
        )

    async def test_second_stage_failure_reaches_ignore_callback_with_cause(self):
        rendered: list[int] = []
        ocr = OCR(
            DeepSeekOCRVendorConfig(
                base_url="https://example.invalid/v1",
                api_key="key",
                model="model",
                retry_times=0,
            ),
            cast(Any, _Handler(rendered)),
        )
        ignored: list[OCRError] = []
        original = RuntimeError("second-stage transport")

        async def request(_runtime, vendor_request):
            if vendor_request.page_index == 2 and vendor_request.stage_index == 2:
                raise OperationError("stage two failed", cause=original)
            return VendorOCRResponse({}, raw_text="", input_tokens=1, output_tokens=1)

        def ignore(error: OCRError) -> bool:
            ignored.append(error)
            return True

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
            root = Path(directory)
            events = [
                event
                async for event in ocr.recognize_vendor(
                    ConcurrentExecutor(FixedCapacity(3)),
                    pdf_path=root / "source.pdf",
                    asset_path=root / "assets",
                    ocr_path=root / "ocr",
                    includes_footnotes=True,
                    ignore_ocr_errors=ignore,
                )
            ]

        self.assertEqual(len(ignored), 1)
        self.assertTrue(all(error.step_index == 2 for error in ignored))
        self.assertTrue(all(error.__cause__ is not None for error in ignored))
        self.assertEqual(
            len([event for event in events if event.kind == OCREventKind.FAILED]),
            1,
        )

    async def test_second_stage_prepare_failure_is_stage_two_in_both_paths(self):
        original_prepare = ocr_module._prepare_second_stage

        for max_tokens in (None, 100):
            with self.subTest(max_tokens=max_tokens):
                rendered: list[int] = []
                ocr = OCR(
                    DeepSeekOCRVendorConfig(
                        base_url="https://example.invalid/v1",
                        api_key="key", model="model", retry_times=0,
                    ),
                    cast(Any, _Handler(rendered)),
                )
                ignored: list[OCRError] = []

                async def request(_runtime, _vendor_request):
                    return VendorOCRResponse(
                        {}, raw_text="", input_tokens=1, output_tokens=1,
                    )

                def prepare(item, *args, **kwargs):
                    if item.page_index == 2:
                        raise RuntimeError("cannot save redacted image")
                    return original_prepare(item, *args, **kwargs)

                def ignore(error: OCRError) -> bool:
                    ignored.append(error)
                    return True

                with tempfile.TemporaryDirectory() as directory, patch.object(
                    VendorOCRRuntime, "_request_deepseek", new=request,
                ), patch.object(
                    ocr_module, "_prepare_second_stage", new=prepare,
                ):
                    root = Path(directory)
                    events = [
                        event
                        async for event in ocr.recognize_vendor(
                            ConcurrentExecutor(FixedCapacity(3)),
                            pdf_path=root / "source.pdf",
                            asset_path=root / "assets",
                            ocr_path=root / "ocr",
                            includes_footnotes=True,
                            ignore_ocr_errors=ignore,
                            max_tokens=max_tokens,
                        )
                    ]

                self.assertEqual(len(ignored), 1)
                self.assertEqual(ignored[0].step_index, 2, max_tokens)
                self.assertEqual(
                    [event.page_index for event in events
                     if event.kind == OCREventKind.FAILED],
                    [2],
                )

    async def test_two_stage_finish_failure_is_stage_two_in_both_paths(self):
        for max_tokens in (None, 100):
            with self.subTest(max_tokens=max_tokens):
                rendered: list[int] = []
                ocr = OCR(
                    DeepSeekOCRVendorConfig(
                        base_url="https://example.invalid/v1",
                        api_key="key", model="model", retry_times=0,
                    ),
                    cast(Any, _Handler(rendered)),
                )
                ignored: list[OCRError] = []

                async def request(_runtime, _vendor_request):
                    return VendorOCRResponse(
                        {}, raw_text="", input_tokens=1, output_tokens=1,
                    )

                def fail_finish(*_args, **_kwargs):
                    raise RuntimeError("cannot assemble OCR page")

                with tempfile.TemporaryDirectory() as directory, patch.object(
                    VendorOCRRuntime, "_request_deepseek", new=request,
                ), patch.object(
                    ocr, "_finish_vendor_results", new=fail_finish,
                ):
                    root = Path(directory)
                    with self.assertRaises(NoUsableOCRPagesError):
                        async for _ in ocr.recognize_vendor(
                            ConcurrentExecutor(FixedCapacity(3)),
                            pdf_path=root / "source.pdf",
                            asset_path=root / "assets",
                            ocr_path=root / "ocr",
                            includes_footnotes=True,
                            ignore_ocr_errors=(
                                lambda error: ignored.append(error) or True
                            ),
                            max_tokens=max_tokens,
                        ):
                            pass

                self.assertEqual(len(ignored), 3)
                self.assertTrue(
                    all(error.step_index == 2 for error in ignored),
                    (max_tokens, [error.step_index for error in ignored]),
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
        requested: list[int] = []

        async def request(_runtime, vendor_request):
            requested.append(vendor_request.page_index)
            return VendorOCRResponse({}, raw_text="", input_tokens=1, output_tokens=1)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
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

        self.assertEqual(requested, [1])

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
        requested: list[int] = []

        async def request(_runtime, vendor_request):
            requested.append(vendor_request.page_index)
            return VendorOCRResponse({}, raw_text="", input_tokens=0, output_tokens=1)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
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

        self.assertEqual(requested, [1])

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
        failed = False

        async def request(_runtime, vendor_request):
            nonlocal failed
            if vendor_request.page_index == 2 and not failed:
                failed = True
                raise OCRError("failed", page_index=2, step_index=1)
            return VendorOCRResponse({}, raw_text="", input_tokens=1, output_tokens=1)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            VendorOCRRuntime, "_request_deepseek", new=request,
        ):
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
                async def request(
                    _runtime, _vendor_request, error_type=error_type,
                ):
                    raise error_type()

                with tempfile.TemporaryDirectory() as directory, patch.object(
                    VendorOCRRuntime, "_request_deepseek", new=request,
                ):
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
