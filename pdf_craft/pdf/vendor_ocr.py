"""Async vendor OCR transports whose capacity covers network operations only."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import httpx

from ..concurrency import (
    AsyncExecutor,
    NonContinuableError,
    OperationError,
    RateLimitedError,
)
from ..error import OCRBillingError, OCRFatalError
from ..metering import AbortedCheck, check_aborted
from ..ocr_config import (
    DeepSeekOCR2VendorConfig,
    DeepSeekOCRVendorConfig,
    UnlimitedOCRVendorConfig,
    VendorOCRConfig,
)
from ..runtime import IO_DOMAIN


@dataclass(frozen=True)
class VendorOCRInput:
    page_index: int
    image_path: Path
    aborted: AbortedCheck


@dataclass(frozen=True)
class VendorOCRResponse:
    data: dict[str, Any]
    raw_text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class VendorOCRResult:
    request: VendorOCRInput
    response: VendorOCRResponse | None = None
    error: OperationError | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class VendorOCRRuntime:
    """One event-loop-bound vendor client with retry outside executor leases."""

    def __init__(self, config: VendorOCRConfig, executor: AsyncExecutor) -> None:
        self.config = config
        self.executor = executor
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def request(self, request: VendorOCRInput) -> VendorOCRResponse:
        async for result in self.request_many((request,)):
            if result.error is not None:
                raise result.error
            assert result.response is not None
            return result.response
        raise RuntimeError("Vendor OCR request did not produce a result")

    async def request_many(
        self, requests: Iterable[VendorOCRInput],
    ) -> AsyncIterator[VendorOCRResult]:
        def initial_requests():
            yield from enumerate(requests)

        pending: Iterable[tuple[int, VendorOCRInput]] = initial_requests()
        attempts: dict[int, int] = {}
        while True:
            scheduled: dict[int, tuple[int, VendorOCRInput]] = {}

            def operations():
                for order, request in pending:
                    async def invoke(
                        operation_id: int,
                        order=order,
                        request=request,
                    ):
                        scheduled[operation_id] = (order, request)
                        response = await self._request_once(request)
                        return operation_id, response
                    yield invoke

            retried: list[tuple[int, VendorOCRInput]] = []
            delays: list[float] = []
            async with aclosing(self.executor.map(operations())) as results:
                async for result in results:
                    order, request = scheduled.pop(result.operation_id)
                    if result.succeeded:
                        assert result.value is not None
                        yield VendorOCRResult(request, response=result.value)
                        continue
                    error = result.error
                    assert error is not None
                    attempts[order] = attempts.get(order, 0) + 1
                    if (
                        _is_retryable(error)
                        and attempts[order] <= self.config.retry_times
                    ):
                        retried.append((order, request))
                        delays.append(
                            error.retry_after
                            if isinstance(error, RateLimitedError)
                            and error.retry_after is not None
                            else self.config.retry_interval_seconds
                        )
                        continue
                    yield VendorOCRResult(request, error=error)
            if not retried:
                break
            pending = retried
            delay = max(delays, default=self.config.retry_interval_seconds)
            if delay > 0:
                await asyncio.sleep(delay)

    async def _request_once(self, request: VendorOCRInput) -> VendorOCRResponse:
        check_aborted(request.aborted)
        try:
            if isinstance(self.config, (
                DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig,
            )):
                return await self._request_deepseek(request)
            if isinstance(self.config, UnlimitedOCRVendorConfig):
                return await self._request_unlimited(request)
            raise TypeError(
                f"Unsupported vendor OCR config: {type(self.config).__name__}"
            )
        except (NonContinuableError, OperationError):
            raise
        except Exception as error:
            raise OperationError(
                str(error) or type(error).__name__, cause=error,
            ) from error

    async def _request_deepseek(
        self, request: VendorOCRInput,
    ) -> VendorOCRResponse:
        config = self.config
        assert isinstance(config, (
            DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig,
        ))
        image = await IO_DOMAIN.run(request.image_path.read_bytes)
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                            + base64.b64encode(image).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        "text": "<image>\n<|grounding|>Convert the document to markdown.",
                    },
                ],
            }],
            "max_tokens": config.max_tokens,
            "stream": False,
        }
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.top_p is not None:
            payload["top_p"] = config.top_p
        client = self._require_client()
        response = await client.post(
            _chat_completions_url(config.base_url),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "pdf-craft-vendor-ocr/1.0",
            },
            json=payload,
        )
        data = _checked_response(response, request.page_index, "DeepSeek OCR")
        usage = data.get("usage") or {}
        choices = data.get("choices") or []
        raw_text = ""
        if choices:
            raw_text = str((choices[0].get("message") or {}).get("content") or "")
        return VendorOCRResponse(
            data=data,
            raw_text=raw_text,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def _request_unlimited(
        self, request: VendorOCRInput,
    ) -> VendorOCRResponse:
        config = self.config
        assert isinstance(config, UnlimitedOCRVendorConfig)
        token = await self._get_access_token(request.page_index)
        encoded = base64.b64encode(
            await IO_DOMAIN.run(request.image_path.read_bytes)
        ).decode("ascii")
        submit = await self._post_form(
            self._unlimited_url(
                "/rest/2.0/brain/online/v2/unlimited-ocr-parser/task", token,
            ),
            {
                "file_data": encoded,
                "file_name": request.image_path.name,
            },
            request.page_index,
            "Unlimited OCR submit",
        )
        task_id = str((submit.get("result") or {}).get("task_id") or "")
        if not task_id:
            raise OperationError(
                f"Unlimited OCR submit response did not include task_id: {submit}"
            )
        deadline = time.monotonic() + config.timeout_seconds
        while True:
            check_aborted(request.aborted)
            data = await self._post_form(
                self._unlimited_url(
                    "/rest/2.0/brain/online/v2/unlimited-ocr-parser/task/query",
                    token,
                ),
                {"task_id": task_id},
                request.page_index,
                "Unlimited OCR query",
            )
            result = data.get("result") or {}
            status = result.get("status")
            parse_url = str(result.get("parse_result_url") or "")
            if status == "success" or parse_url:
                if not parse_url:
                    raise OperationError(
                        f"Unlimited OCR task {task_id} did not return parse_result_url"
                    )
                response = await self._require_client().get(
                    parse_url,
                    headers={"User-Agent": "pdf-craft-vendor-ocr/1.0"},
                )
                parsed = _checked_response(
                    response, request.page_index, "Unlimited OCR download",
                )
                return VendorOCRResponse(data={
                    "task_id": task_id,
                    "status": status,
                    "parse_result": parsed,
                })
            if status == "failed":
                raise OperationError(f"Unlimited OCR task {task_id} failed: {result}")
            if time.monotonic() >= deadline:
                raise OperationError(f"Unlimited OCR task {task_id} timed out")
            await asyncio.sleep(config.poll_interval_seconds)

    async def _get_access_token(self, page_index: int) -> str:
        if self._access_token is not None:
            return self._access_token
        async with self._token_lock:
            if self._access_token is not None:
                return self._access_token
            config = self.config
            assert isinstance(config, UnlimitedOCRVendorConfig)
            response = await self._require_client().post(
                f"{config.base_url.rstrip('/')}/oauth/2.0/token",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "pdf-craft-vendor-ocr/1.0",
                },
                data={
                    "grant_type": "client_credentials",
                    "client_id": config.ak,
                    "client_secret": config.sk,
                },
            )
            data = _checked_response(response, page_index, "Unlimited OCR token")
            token = str(data.get("access_token") or "")
            if not token:
                raise OperationError(
                    f"Unlimited OCR token response did not include access_token: {data}"
                )
            self._access_token = token
            return token

    async def _post_form(
        self,
        url: str,
        data: dict[str, str],
        page_index: int,
        action: str,
    ) -> dict[str, Any]:
        response = await self._require_client().post(
            url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "pdf-craft-vendor-ocr/1.0",
            },
            data=data,
        )
        result = _checked_response(response, page_index, action)
        if int(result.get("error_code") or 0) != 0:
            raise OperationError(f"{action} request failed: {result}")
        return result

    def _unlimited_url(self, path: str, token: str) -> str:
        config = self.config
        assert isinstance(config, UnlimitedOCRVendorConfig)
        return f"{config.base_url.rstrip('/')}{path}?access_token={token}"

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("VendorOCRRuntime must be entered before requests")
        return self._client


VendorOCRRequest = Callable[[VendorOCRInput], Awaitable[VendorOCRResponse]]


@asynccontextmanager
async def create_vendor_ocr_request(
    config: VendorOCRConfig,
    executor: AsyncExecutor,
) -> AsyncIterator[VendorOCRRequest]:
    """Bind one vendor configuration and executor into a retrying request."""
    async with VendorOCRRuntime(config, executor) as runtime:
        yield runtime.request


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _checked_response(
    response: httpx.Response,
    page_index: int,
    action: str,
) -> dict[str, Any]:
    if response.status_code >= 400:
        message = _response_message(response)
        if response.status_code == 429:
            raise RateLimitedError(
                f"{action} was rate limited: {message}",
                retry_after=_retry_after(response),
            )
        if response.status_code == 402:
            raise OCRBillingError(page_index)
        if response.status_code in (401, 403):
            raise OCRFatalError(
                f"{action} authorization failed with HTTP {response.status_code}: "
                f"{message}"
            )
        error = httpx.HTTPStatusError(
            f"{action} failed with HTTP {response.status_code}: {message}",
            request=response.request,
            response=response,
        )
        raise OperationError(str(error), cause=error) from error
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise OperationError(f"{action} returned invalid JSON", cause=error) from error
    if not isinstance(data, dict):
        raise OperationError(f"{action} returned a non-object JSON response")
    return data


def _response_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, str):
        return body[:500]
    return json.dumps(body, ensure_ascii=False)[:500]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _is_retryable(error: OperationError) -> bool:
    if isinstance(error, RateLimitedError):
        return True
    cause = error.__cause__
    if isinstance(cause, httpx.TransportError):
        return True
    return (
        isinstance(cause, httpx.HTTPStatusError)
        and cause.response.status_code >= 500
    )
