"""Provider entry points normalize limits; executors own their recovery."""
# pylint: disable=protected-access,cell-var-from-loop
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig,
    FixedCapacity, JEV, LLM, RateLimitedError, RateLimitScope, RateLimitTimeoutError,
    RetryPolicy, UnlimitedOCRVendorConfig,
)
from pdf_craft.jev import JEVRuntime, _provider_error as jev_error
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.pdf.vendor_ocr import VendorOCRRuntime, VendorOCRInput
from pdf_craft.rate_limit import rate_limit_request
from tests.test_rate_limit import VirtualTime


class ProviderRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_stream_error_retries_with_transport_budget_zero(self):
        with VirtualTime() as clock:
            calls = 0
            def handler(request):
                nonlocal calls
                calls += 1
                data = ('{"error":{"code":"rate_limit_exceeded","type":"rate_limit_error","message":"slow"}}'
                        if calls == 1 else '{"choices":[{"index":0,"delta":{"content":"translated"}}]}')
                return httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'},
                                      text=f'data: {data}\n\ndata: [DONE]\n\n')
            original = openai.AsyncOpenAI
            def client(**kwargs):
                kwargs['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                return original(**kwargs)
            runtime = LLMRuntime(LLM('key', 'https://example.invalid/v1', 'model', 'o200k_base', retry_times=0),
                                 executor=ConcurrentExecutor(FixedCapacity(1)))
            with patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', side_effect=client):
                self.assertEqual(await clock.finish(runtime.request('source')), 'translated')
            self.assertEqual(calls, 2)

    async def test_llm_http_429_date_and_timeout_root(self):
        with VirtualTime() as clock:
            root = openai.RateLimitError('slow', response=httpx.Response(
                429, request=httpx.Request('POST', 'https://example.invalid'), headers={'Retry-After': '30'}),
                body={'error': {'type': 'rate_limit_error'}})
            runtime = LLMRuntime(LLM('key', 'https://example.invalid', 'model', 'o200k_base', retry_times=0),
                                 executor=ConcurrentExecutor(FixedCapacity(1),
                                     rate_limit_scope=RateLimitScope(max_wait_seconds=2)))
            invoke = AsyncMock(side_effect=root)
            setattr(runtime, '_invoke', invoke)
            # Scope timeout is not wrapped or retried as a transport failure.
            with self.assertRaises(RateLimitTimeoutError) as caught:
                await clock.finish(runtime.request('source'))
            timeout = caught.exception
            self.assertIsInstance(timeout, RateLimitTimeoutError)
            assert timeout.__cause__ is not None
            self.assertIs(timeout.__cause__.__cause__, root)
            self.assertEqual(invoke.await_count, 1)

    async def test_jev_zero_analysis_budget_still_recovers_limit(self):
        with VirtualTime() as clock:
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            limit = RateLimitedError(retry_after=2)
            client.system_one = AsyncMock(side_effect=[
                limit,
                SimpleNamespace(nouls={'page_passes_strict_standard': SimpleNamespace(noul=.9)}),
            ])
            with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=client):
                async with JEVRuntime(JEV('key'), ConcurrentExecutor(FixedCapacity(1)),
                                      retry_policy=RetryPolicy(analysis=0)) as runtime:
                    self.assertEqual(await clock.finish(runtime.evaluate(1, {'state': {}, 'questions': {}})), .9)
            self.assertIsNone(limit.__cause__, 'normalization must not cause a self-cycle')
            normalized = jev_error(SimpleJEVLimit())
            assert isinstance(normalized, RateLimitedError)
            self.assertEqual(normalized.retry_after, 2)

    async def test_deepseek_both_versions_zero_retry_budget(self):
        for config_type in (DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig):
            with self.subTest(config=config_type), VirtualTime() as clock:
                calls = 0
                def handler(request):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return httpx.Response(429, request=request, headers={'Retry-After': '2'}, json={})
                    return httpx.Response(200, request=request, json={'choices': [{'message': {'content': 'ok'}}]})
                async def resolve(_path):
                    return 'https://example.invalid/image.png'
                async with VendorOCRRuntime(config_type('https://example.invalid', 'key', 'model', retry_times=0),
                                            ConcurrentExecutor(FixedCapacity(1)), resolve) as runtime:
                    assert runtime._client is not None
                    await runtime._client.aclose()
                    runtime._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                    result = await clock.finish(runtime.request(VendorOCRInput(1, Path('unused'), lambda: False)))
                    self.assertEqual(result.raw_text, 'ok')
                    self.assertEqual(calls, 2)

    async def test_unlimited_each_http_boundary_recovers_with_zero_retries(self):
        # Exercise the real token -> submit -> query -> download graph, one
        # limited HTTP boundary per case (including HTTP200 business limits).
        for limited in ('token', 'submit', 'query', 'download', 'business'):
            with self.subTest(boundary=limited), VirtualTime() as clock:
                calls = Counter()
                def handler(request):
                    if 'oauth' in request.url.path:
                        stage, result = 'token', {'access_token': 'token'}
                    elif request.url.path == '/download':
                        stage, result = 'download', {'pages': []}
                    elif request.url.path.endswith('/task'):
                        stage, result = 'submit', {'error_code': 0, 'result': {'task_id': 'task'}}
                    else:
                        stage, result = 'query', {'error_code': 0, 'result': {
                            'status': 'success', 'parse_result_url': 'https://example.invalid/download'}}
                    calls[stage] += 1
                    if stage == limited and calls[stage] == 1:
                        return httpx.Response(429, request=request, headers={'Retry-After': '1'}, json={})
                    if limited == 'business' and stage == 'submit' and calls[stage] == 1:
                        result = {'error_code': 18, 'error_msg': 'limited'}
                    return httpx.Response(200, request=request, json=result)
                async def resolve(_path):
                    return 'https://example.invalid/image.png'
                async with VendorOCRRuntime(UnlimitedOCRVendorConfig('ak', 'sk', retry_times=0),
                                            ConcurrentExecutor(FixedCapacity(1)), resolve) as runtime:
                    assert runtime._client is not None
                    await runtime._client.aclose()
                    runtime._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                    result = await clock.finish(runtime.request(VendorOCRInput(1, Path('unused'), lambda: False)))
                    self.assertEqual(result.data['status'], 'success')
                self.assertEqual(calls['submit' if limited == 'business' else limited], 2)

    async def test_budget_survives_multiple_executor_runs_in_one_request(self):
        with VirtualTime() as clock:
            scope = RateLimitScope(max_wait_seconds=4)
            executor = ConcurrentExecutor(FixedCapacity(1), rate_limit_scope=scope)
            calls = 0
            async def limit_then_success():
                nonlocal calls
                calls += 1
                if calls % 2:
                    raise RateLimitedError(retry_after=2)
                return 'ok'
            @rate_limit_request
            async def logical_request():
                await executor.run(limit_then_success)
                # Represents a non-rate retry / next provider I/O boundary.
                return await executor.run(limit_then_success)
            with self.assertRaises(RateLimitTimeoutError):
                await clock.finish(logical_request())
            self.assertEqual(calls, 3)


class SimpleJEVLimit(Exception):
    status = 429
    retry_after_ms = 2000
