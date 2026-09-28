"""Async vendor OCR transports whose capacity covers network operations only."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Never, Self

import httpx
from doc_page_extractor.errors import VendorOCRRequestError

from ..concurrency import (
    AsyncExecutor,
    NonContinuableError,
    OperationError,
    RateLimitedError,
    task_group_fatal_error,
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
    stage_index: int = 1


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


class _UnlimitedTokenExpiredError(OperationError):
    """The current cached access token must be replaced before retrying."""


class _UnlimitedResubmitError(OperationError):
    """The provider discarded a task, so the page must be submitted again."""


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

    async def request(
        self,
        request: VendorOCRInput,
        *,
        _started: asyncio.Event | None = None,
    ) -> VendorOCRResponse:
        if isinstance(self.config, UnlimitedOCRVendorConfig):
            return await self._request_once(request, _started)
        try:
            return await self._request_once(request, _started)
        except NonContinuableError:
            raise
        except OperationError as error:
            if not _is_retryable(error) or self.config.retry_times == 0:
                raise
            return await self._retry_request_after_error(
                request, error, attempts_used=1,
            )

    async def request_many(
        self, requests: Iterable[VendorOCRInput],
    ) -> AsyncIterator[VendorOCRResult]:
        iterator = iter(requests)
        tasks: dict[asyncio.Task[VendorOCRResponse], VendorOCRInput] = {}
        admission: asyncio.Task[bool] | None = None
        admission_owner: asyncio.Task[VendorOCRResponse] | None = None
        exhausted = False

        def start_next() -> None:
            nonlocal admission, admission_owner, exhausted
            if exhausted:
                return
            try:
                request = next(iterator)
            except StopIteration:
                exhausted = True
                return
            started = asyncio.Event()
            task = asyncio.create_task(self.request(request, _started=started))
            tasks[task] = request
            admission_owner = task
            admission = asyncio.create_task(started.wait())

        try:
            start_next()
            while tasks:
                pending: set[asyncio.Task[Any]] = set(tasks)
                if admission is not None:
                    pending.add(admission)
                done, _ = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task not in tasks or not task.cancelled():
                        continue
                    try:
                        task.result()
                    except asyncio.CancelledError as error:
                        fatal = next((
                            argument for argument in error.args
                            if isinstance(argument, NonContinuableError)
                        ), None)
                        if fatal is not None:
                            raise fatal from error
                        raise
                fatal = task_group_fatal_error(
                    task for task in done
                    if task in tasks and not task.cancelled()
                )
                if fatal is not None:
                    raise fatal

                owner_finished_before_admission = (
                    admission_owner is not None
                    and admission_owner in done
                    and admission is not None
                    and admission not in done
                )
                admitted = admission is not None and admission in done
                if admitted or owner_finished_before_admission:
                    if admission is not None:
                        admission.cancel()
                        await asyncio.gather(admission, return_exceptions=True)
                    admission = None
                    admission_owner = None
                    start_next()

                for task in done:
                    if task not in tasks:
                        continue
                    request = tasks.pop(task)
                    try:
                        yield VendorOCRResult(request, response=task.result())
                    except NonContinuableError:
                        raise
                    except OperationError as error:
                        yield VendorOCRResult(request, error=error)
        finally:
            if admission is not None:
                admission.cancel()
            for task in tasks:
                task.cancel()
            teardown: list[asyncio.Task[Any]] = list(tasks)
            if admission is not None:
                teardown.append(admission)
            if teardown:
                await asyncio.gather(*teardown, return_exceptions=True)
            close = getattr(iterator, "close", None)
            if close is not None:
                close()

    async def _retry_request_after_error(
        self,
        request: VendorOCRInput,
        error: OperationError,
        *,
        attempts_used: int,
    ) -> VendorOCRResponse:
        current = error
        while attempts_used <= self.config.retry_times:
            delay = (
                current.retry_after
                if isinstance(current, RateLimitedError)
                and current.retry_after is not None
                else self.config.retry_interval_seconds
            )
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                return await self._request_once(request)
            except NonContinuableError:
                raise
            except OperationError as next_error:
                current = next_error
                attempts_used += 1
                if not _is_retryable(current):
                    raise
        raise current

    async def _request_once(
        self,
        request: VendorOCRInput,
        started: asyncio.Event | None = None,
    ) -> VendorOCRResponse:
        try:
            check_aborted(request.aborted)
            if isinstance(self.config, (
                DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig,
            )):
                return await self._request_deepseek(request, started)
            if isinstance(self.config, UnlimitedOCRVendorConfig):
                return await self._request_unlimited(request, started)
            raise TypeError(
                f"Unsupported vendor OCR config: {type(self.config).__name__}"
            )
        except (NonContinuableError, OperationError):
            raise
        except httpx.HTTPError as error:
            envelope = _vendor_error(
                str(error) or type(error).__name__, error,
            )
            raise OperationError(str(envelope), cause=envelope) from envelope
        except Exception as error:
            from doc_page_extractor.extraction_context import (
                ExtractionAbortedError,
            )
            if isinstance(error, ExtractionAbortedError):
                raise
            if isinstance(self.config, UnlimitedOCRVendorConfig):
                envelope = _vendor_error(
                    str(error) or type(error).__name__, error,
                )
                raise OperationError(
                    str(envelope), cause=envelope,
                ) from envelope
            raise OperationError(
                str(error) or type(error).__name__, cause=error,
            ) from error

    async def _request_deepseek(
        self,
        request: VendorOCRInput,
        started: asyncio.Event | None,
    ) -> VendorOCRResponse:
        config = self.config
        assert isinstance(config, (
            DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig,
        ))
        payload = await IO_DOMAIN.run(
            _deepseek_payload, request.image_path, config,
        )

        async def send() -> dict[str, Any]:
            if started is not None:
                started.set()
            response = await self._require_client().post(
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
            return {"data": data, "response": response}

        sent = await self.executor.run(send)
        data = sent["data"]
        response = sent["response"]
        assert isinstance(data, dict)
        assert isinstance(response, httpx.Response)
        usage_value = data.get("usage")
        if usage_value is not None and not isinstance(usage_value, dict):
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid usage",
            )
        usage = usage_value or {}
        choices_value = data.get("choices")
        if not isinstance(choices_value, list) or not choices_value:
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid choices",
            )
        choice = choices_value[0]
        if not isinstance(choice, dict):
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid choice",
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid message",
            )
        content = message.get("content")
        if not isinstance(content, str):
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid content",
            )
        token_counts = (
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in token_counts
        ):
            _raise_malformed_response(
                response, "DeepSeek OCR response has invalid token usage",
            )
        return VendorOCRResponse(
            data=data,
            raw_text=content,
            input_tokens=token_counts[0],
            output_tokens=token_counts[1],
        )

    async def _request_unlimited(
        self,
        request: VendorOCRInput,
        started: asyncio.Event | None,
    ) -> VendorOCRResponse:
        config = self.config
        assert isinstance(config, UnlimitedOCRVendorConfig)
        resubmissions = 0
        while True:
            try:
                return await self._request_unlimited_task(request, started)
            except _UnlimitedResubmitError:
                if resubmissions >= config.retry_times:
                    raise
                if config.retry_interval_seconds > 0:
                    await asyncio.sleep(config.retry_interval_seconds)
                resubmissions += 1

    async def _request_unlimited_task(
        self,
        request: VendorOCRInput,
        started: asyncio.Event | None,
    ) -> VendorOCRResponse:
        config = self.config
        assert isinstance(config, UnlimitedOCRVendorConfig)
        submit = await self._submit_unlimited(request, started)
        submit_result = submit["result"]
        assert isinstance(submit_result, dict)
        task_id = str(submit_result["task_id"])
        deadline = time.monotonic() + config.timeout_seconds
        while True:
            check_aborted(request.aborted)
            data = await self._run_unlimited_with_token(
                request.page_index,
                lambda token: self._post_form(
                    self._unlimited_url(
                        "/rest/2.0/brain/online/v2/unlimited-ocr-parser/task/query",
                        token,
                    ),
                    {"task_id": task_id},
                    request.page_index,
                    "Unlimited OCR query",
                    result_kind="query",
                ),
            )
            result = data["result"]
            assert isinstance(result, dict)
            status = result.get("status")
            parse_url = str(result.get("parse_result_url") or "")
            if status == "success":
                async def download() -> dict[str, Any]:
                    response = await self._require_client().get(
                        parse_url,
                        headers={"User-Agent": "pdf-craft-vendor-ocr/1.0"},
                    )
                    return _checked_response(
                        response, request.page_index, "Unlimited OCR download",
                    )

                parsed = await self._run_io_with_retry(download)
                return VendorOCRResponse(data={
                    "task_id": task_id,
                    "status": status,
                    "parse_result": parsed,
                })
            if time.monotonic() >= deadline:
                raise OperationError(f"Unlimited OCR task {task_id} timed out")
            await asyncio.sleep(config.poll_interval_seconds)

    async def _submit_unlimited(
        self,
        request: VendorOCRInput,
        started: asyncio.Event | None,
    ) -> dict[str, Any]:
        config = self.config
        assert isinstance(config, UnlimitedOCRVendorConfig)
        retry_attempt = 0
        token_attempt = 0
        while True:
            token = await self._get_access_token(request.page_index)
            try:
                return await self._submit_unlimited_once(
                    request, token, started,
                )
            except _UnlimitedTokenExpiredError:
                await self._invalidate_access_token(token)
                if token_attempt >= config.retry_times:
                    raise
                token_attempt += 1
            except NonContinuableError:
                raise
            except OperationError as error:
                if not _is_retryable(error) or retry_attempt >= config.retry_times:
                    raise
                retry_attempt += 1
                rate_limit = error if isinstance(error, RateLimitedError) else None
                delay = (
                    rate_limit.retry_after
                    if rate_limit is not None
                    and rate_limit.retry_after is not None
                    else config.retry_interval_seconds
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            if config.retry_interval_seconds > 0:
                await asyncio.sleep(config.retry_interval_seconds)

    async def _submit_unlimited_once(
        self,
        request: VendorOCRInput,
        token: str,
        started: asyncio.Event | None,
    ) -> dict[str, Any]:
        encoded = await IO_DOMAIN.run(_encode_image, request.image_path)

        async def submit() -> dict[str, Any]:
            return await self._post_form(
                self._unlimited_url(
                    "/rest/2.0/brain/online/v2/unlimited-ocr-parser/task",
                    token,
                ),
                {
                    "file_data": encoded,
                    "file_name": request.image_path.name,
                },
                request.page_index,
                "Unlimited OCR submit",
                result_kind="submit",
            )

        return await self._run_io_once(submit, started)

    async def _run_unlimited_with_token(
        self,
        page_index: int,
        operation: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            token = await self._get_access_token(page_index)
            try:
                return await self._run_io_with_retry(lambda: operation(token))
            except _UnlimitedTokenExpiredError:
                await self._invalidate_access_token(token)
                if attempt >= self.config.retry_times:
                    raise
                if self.config.retry_interval_seconds > 0:
                    await asyncio.sleep(self.config.retry_interval_seconds)
                attempt += 1

    async def _invalidate_access_token(self, expired_token: str) -> None:
        async with self._token_lock:
            if self._access_token == expired_token:
                self._access_token = None

    async def _get_access_token(self, page_index: int) -> str:
        if self._access_token is not None:
            return self._access_token
        async with self._token_lock:
            if self._access_token is not None:
                return self._access_token
            config = self.config
            assert isinstance(config, UnlimitedOCRVendorConfig)
            async def fetch_token() -> dict[str, Any]:
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
                data = _checked_response(
                    response, page_index, "Unlimited OCR token",
                )
                oauth_error = str(data.get("error") or "")
                if oauth_error:
                    _raise_unlimited_oauth_error(
                        page_index, oauth_error, data, response,
                    )
                access_token = data.get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    _raise_malformed_response(
                        response,
                        "Unlimited OCR token response did not include a valid "
                        "access_token",
                    )
                return data

            data = await self._run_io_with_retry(fetch_token)
            token = data["access_token"]
            assert isinstance(token, str)
            self._access_token = token
            return token

    async def _post_form(
        self,
        url: str,
        data: dict[str, str],
        page_index: int,
        action: str,
        *,
        result_kind: Literal["submit", "query"],
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
        error_code_value = result.get("error_code", 0)
        try:
            error_code = int(error_code_value)
        except (TypeError, ValueError):
            _raise_malformed_response(
                response, f"{action} response has invalid error_code",
            )
        if error_code != 0:
            _raise_unlimited_error(
                action, page_index, error_code, result, response,
            )
        provider_result = result.get("result")
        if not isinstance(provider_result, dict):
            message = f"{action} response has invalid result: {result}"
            _raise_malformed_response(response, message)
        if result_kind == "submit":
            task_id = provider_result.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                _raise_malformed_response(
                    response, f"{action} response did not include a valid task_id",
                )
        else:
            status = provider_result.get("status")
            if not isinstance(status, str) or status not in {
                "pending", "running", "success", "failed",
            }:
                _raise_malformed_response(
                    response, f"{action} response has invalid status",
                )
            parse_url = provider_result.get("parse_result_url")
            if status == "success" and (
                not isinstance(parse_url, str) or not parse_url
            ):
                _raise_malformed_response(
                    response,
                    f"{action} success response did not include parse_result_url",
                )
            if status == "failed":
                task_error = provider_result.get("task_error")
                if not isinstance(task_error, str) or not task_error:
                    _raise_malformed_response(
                        response,
                        f"{action} failed response did not include task_error",
                    )
                _raise_unlimited_task_error(
                    page_index, action, task_error, result, response,
                )
        return result

    async def _run_io_with_retry(
        self,
        operation: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return await self._run_io_once(operation)
            except NonContinuableError:
                raise
            except OperationError as error:
                if not _is_retryable(error) or attempt >= self.config.retry_times:
                    raise
                rate_limit = error if isinstance(error, RateLimitedError) else None
                delay = (
                    rate_limit.retry_after
                    if rate_limit is not None
                    and rate_limit.retry_after is not None
                    else self.config.retry_interval_seconds
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1

    async def _run_io_once(
        self,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        started: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        async def invoke() -> dict[str, Any]:
            if started is not None:
                started.set()
            return await _invoke_vendor_io(operation)

        return await self.executor.run(invoke)

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


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _deepseek_payload(
    image_path: Path,
    config: DeepSeekOCRVendorConfig | DeepSeekOCR2VendorConfig,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64," + _encode_image(image_path),
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
    return payload


def _checked_response(
    response: httpx.Response,
    page_index: int,
    action: str,
) -> dict[str, Any]:
    if response.status_code >= 400:
        message = _response_message(response)
        raw_error = httpx.HTTPStatusError(
            f"{action} failed with HTTP {response.status_code}: {message}",
            request=response.request,
            response=response,
        )
        envelope = _vendor_error(str(raw_error), raw_error)
        if response.status_code == 429:
            if _is_quota_response(response):
                error = OCRBillingError(page_index)
                raise error from envelope
            raise RateLimitedError(
                f"{action} was rate limited: {message}",
                retry_after=_retry_after(response),
                cause=envelope,
            ) from envelope
        if response.status_code == 402:
            error = OCRBillingError(page_index)
            raise error from envelope
        if response.status_code in (401, 403):
            error = OCRFatalError(
                f"{action} authorization failed with HTTP {response.status_code}: "
                f"{message}"
            )
            raise error from envelope
        raise OperationError(str(raw_error), cause=envelope) from envelope
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as error:
        _raise_malformed_response(
            response, f"{action} returned invalid JSON", cause=error,
        )
    if not isinstance(data, dict):
        _raise_malformed_response(
            response, f"{action} returned a non-object JSON response",
        )
    return data


def _raise_malformed_response(
    response: httpx.Response,
    message: str,
    *,
    cause: Exception | None = None,
) -> Never:
    raw_error = httpx.HTTPStatusError(
        message, request=response.request, response=response,
    )
    if cause is not None:
        raw_error.__cause__ = cause
    envelope = _vendor_error(message, raw_error)
    raise OperationError(message, cause=envelope) from envelope


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
    cause = _root_cause(error)
    if isinstance(cause, httpx.TransportError):
        return True
    return (
        isinstance(cause, httpx.HTTPStatusError)
        and (
            cause.response.status_code == 408
            or cause.response.status_code >= 500
        )
    )


def _vendor_error(message: str, cause: Exception) -> VendorOCRRequestError:
    error = VendorOCRRequestError(message)
    error.__cause__ = cause
    return error


async def _invoke_vendor_io(
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    try:
        return await operation()
    except (OperationError, NonContinuableError):
        raise
    except Exception as error:
        envelope = _vendor_error(
            str(error) or type(error).__name__, error,
        )
        raise OperationError(str(envelope), cause=envelope) from envelope


def _root_cause(error: BaseException) -> BaseException:
    current = error
    visited: set[int] = set()
    while current.__cause__ is not None and id(current) not in visited:
        visited.add(id(current))
        current = current.__cause__
    return current


def _is_quota_response(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    provider_error = body.get("error")
    if not isinstance(provider_error, dict):
        return False
    code = str(provider_error.get("code") or "").lower()
    error_type = str(provider_error.get("type") or "").lower()
    return code in {"insufficient_quota", "quota_exceeded", "billing_not_active"} or (
        error_type in {"insufficient_quota", "quota_exceeded", "billing_not_active"}
    )


def _raise_unlimited_error(
    action: str,
    page_index: int,
    error_code: int,
    response_data: dict[str, Any],
    response: httpx.Response,
) -> None:
    message = f"{action} request failed ({error_code}): {response_data}"
    raw_error = httpx.HTTPStatusError(
        message, request=response.request, response=response,
    )
    envelope = _vendor_error(message, raw_error)
    if error_code in {1, 2, 4, 18}:
        raise RateLimitedError(message, cause=envelope) from envelope
    if error_code in {17, 19, 282005}:
        error = OCRBillingError(page_index)
        raise error from envelope
    if error_code in {6, 14, 282006}:
        error = OCRFatalError(message)
        raise error from envelope
    if error_code in {100, 110, 111}:
        raise _UnlimitedTokenExpiredError(
            message, cause=envelope,
        ) from envelope
    if error_code == 282000:
        raise _UnlimitedResubmitError(
            message, cause=envelope,
        ) from envelope
    raise OperationError(message, cause=envelope) from envelope


def _raise_unlimited_task_error(
    page_index: int,
    action: str,
    task_error: str,
    response_data: dict[str, Any],
    response: httpx.Response,
) -> Never:
    message = f"{action} task failed ({task_error}): {response_data}"
    raw_error = httpx.HTTPStatusError(
        message, request=response.request, response=response,
    )
    envelope = _vendor_error(message, raw_error)
    normalized = task_error.strip().lower()
    if any(marker in normalized for marker in (
        "额度不够", "额度不足", "配额", "余额不足", "quota",
    )):
        error = OCRBillingError(page_index)
        raise error from envelope
    if normalized == "任务失败":
        raise _UnlimitedResubmitError(
            message, cause=envelope,
        ) from envelope
    raise OperationError(message, cause=envelope) from envelope


def _raise_unlimited_oauth_error(
    page_index: int,
    error_code: str,
    response_data: dict[str, Any],
    response: httpx.Response,
) -> None:
    message = (
        f"Unlimited OCR token request failed ({error_code}): {response_data}"
    )
    raw_error = httpx.HTTPStatusError(
        message, request=response.request, response=response,
    )
    envelope = _vendor_error(message, raw_error)
    if error_code in {
        "access_denied",
        "invalid_client",
        "invalid_grant",
        "unauthorized_client",
    }:
        error = OCRFatalError(
            f"Unlimited OCR authentication failed for page {page_index}: "
            f"{error_code}"
        )
        raise error from envelope
    raise OperationError(message, cause=envelope) from envelope
