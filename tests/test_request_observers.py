"""Attempt observations must not hide retries or change resource adoption."""
# pylint: disable=protected-access
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import httpx2
import openai

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity, JEV, LLM,
    RequestKind, RequestOutcome, RequestUsage, UnlimitedOCRVendorConfig, UsageCollector,
)
from pdf_craft.jev import JEVRuntime
from pdf_craft.llm import runtime_for
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime
from pdf_craft.request_events import observe_response
from pdf_craft.usage import observe_tokens


def llm(callback, **options):
    return LLM('secret', 'https://invalid', 'model', 'o200k_base',
               retry_interval_seconds=0, on_request=callback, **options)


def executor():
    return ConcurrentExecutor(FixedCapacity(3))


class RequestObserversTests(IsolatedAsyncioTestCase):
    async def test_retries_and_no_collector_usage(self):
        events = []
        config = llm(events.append, retry_times=2)
        runtime = runtime_for(config, executor())
        statuses = iter([429, 502, 200])
        async def invoke(*_args):
            status = next(statuses)
            response = httpx.Response(status, request=httpx.Request('POST', 'https://invalid'),
                                      headers={'Retry-After': '0'})
            observe_response(response)
            observe_tokens({'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 4})
            if status != 200:
                raise openai.APIStatusError('private response', response=response, body=None)
            return 'answer'
        setattr(runtime, "_invoke", invoke)
        self.assertEqual(await runtime.request('private prompt'), 'answer')
        self.assertEqual([e.outcome for e in events], [RequestOutcome.RATE_LIMITED,
                          RequestOutcome.SERVER_ERROR, RequestOutcome.SUCCESS])
        self.assertEqual([e.http_status for e in events], [429, 502, 200])
        for event in events:
            self.assertIs(event.provider, config)
            self.assertEqual(event.usage, RequestUsage(1, 10, 4, 2))
            self.assertIs(event.started_at.tzinfo, timezone.utc)
            self.assertGreaterEqual(event.duration_ms, 0)
            self.assertNotIn('private', repr(event))
            self.assertNotIn('secret', repr(event))

    async def test_empty_and_timeout_are_observed_and_cache_is_not(self):
        events = []
        with TemporaryDirectory() as directory:
            runtime = runtime_for(llm(events.append, retry_times=1, cache_path=directory), executor())
            setattr(runtime, "_invoke", AsyncMock(side_effect=['', 'answer']))
            self.assertEqual(await runtime.request('input'), 'answer')
            self.assertEqual(await runtime.request('input'), 'answer')
            self.assertEqual([e.outcome for e in events], [RequestOutcome.INVALID_RESPONSE, RequestOutcome.SUCCESS])
            self.assertIsNone(events[0].usage)
        events.clear()
        runtime = runtime_for(llm(events.append, retry_times=0), executor())
        setattr(runtime, "_invoke", AsyncMock(side_effect=httpx.ReadTimeout('private timeout')))
        with self.assertRaises(Exception):
            await runtime.request('input')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].outcome, RequestOutcome.TIMEOUT)
        self.assertIsNone(events[0].http_status)

    async def test_observer_cannot_retry_or_modify_collected_usage(self):
        events = []
        def callback(event):
            events.append(event)
            event.usage.input_tokens = 999
            raise RuntimeError('private callback exception')
        config = llm(callback)
        runtime = runtime_for(config, executor())
        async def invoke(*_args):
            observe_tokens({'input_tokens': 7})
            return 'answer'
        setattr(runtime, "_invoke", invoke)
        collector = UsageCollector()
        self.assertEqual(await runtime.request('input', usage=collector), 'answer')
        self.assertEqual(len(events), 1)
        self.assertEqual(collector.report()[config].effective.input_tokens, 7)

    async def test_concurrent_context_and_cancellation_are_isolated(self):
        job = ContextVar('job', default='')
        events = []
        config = llm(lambda event: events.append((job.get(), event)))
        runtime = runtime_for(config, executor())
        entered, finish = asyncio.Event(), asyncio.Event()
        async def invoke(*_args):
            if job.get() == 'cancel':
                observe_tokens({'input_tokens': 8})
                entered.set()
                await finish.wait()
            else:
                observe_tokens({'input_tokens': 3})
            return 'answer'
        setattr(runtime, "_invoke", invoke)
        async def request(name):
            token = job.set(name)
            try:
                await runtime.request(name)
            finally:
                job.reset(token)
        cancelled = asyncio.create_task(request('cancel'))
        await entered.wait()
        await request('ok')
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertEqual([name for name, _ in events], ['ok', 'cancel'])
        self.assertEqual([event.usage.input_tokens for _, event in events], [3, 8])
        self.assertEqual(events[1][1].outcome, RequestOutcome.CANCELLED)

    async def test_sdk_stream_usage_is_requested_without_collector_and_disconnect_keeps_status(self):
        events = []
        class Stream:
            response = httpx.Response(200)
            async def __aiter__(self):
                yield SimpleNamespace(choices=[], usage={'input_tokens': 11})
                raise httpx.ReadError('private stream contents')
            async def aclose(self):
                pass
        sdk = MagicMock()
        sdk.__aenter__ = AsyncMock(return_value=sdk)
        sdk.__aexit__ = AsyncMock()
        sdk.chat.completions.create = AsyncMock(return_value=Stream())
        with patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', return_value=sdk):
            with self.assertRaises(Exception):
                await runtime_for(llm(events.append, retry_times=0), executor()).request('input')
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs['stream_options'], {'include_usage': True})
        self.assertEqual(events[0].outcome, RequestOutcome.NETWORK_ERROR)
        self.assertEqual(events[0].http_status, 200)
        self.assertEqual(events[0].usage.input_tokens, 11)

    async def test_jev_invalid_probability_keeps_received_usage(self):
        events = []
        config = JEV('secret', on_request=events.append)
        runtime = JEVRuntime(config, executor())
        runtime._client = MagicMock(system_one=AsyncMock(return_value=SimpleNamespace(
            usage={'input_tokens': 5}, nouls={'page_passes_strict_standard': SimpleNamespace(noul=2)},
        )))
        with self.assertRaisesRegex(Exception, "probability"):
            await runtime.evaluate(1, {'state': {}, 'questions': {}})
        self.assertEqual(events[0].outcome, RequestOutcome.INVALID_RESPONSE)
        self.assertEqual(events[0].usage.input_tokens, 5)
        self.assertIsNone(events[0].http_status)

    async def test_deepseek_bad_protocol_is_not_success(self):
        events = []
        config = DeepSeekOCRVendorConfig('https://invalid', 'secret', 'model',
                                        retry_times=0, on_request=events.append)
        async def resolve(_path):
            return 'https://invalid/image'
        runtime = VendorOCRRuntime(config, executor(), resolve)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, json={'usage': {'prompt_tokens': 4}, 'choices': []}))) as client:
            runtime._client = client
            with self.assertRaises(Exception):
                await runtime.request(VendorOCRInput(1, Path('unused.png'), lambda: False))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].outcome, RequestOutcome.INVALID_RESPONSE)
        self.assertEqual(events[0].http_status, 200)
        self.assertEqual(events[0].usage.input_tokens, 4)

    async def test_unlimited_observes_internal_query_failure_before_retry(self):
        events, queries = [], 0
        config = UnlimitedOCRVendorConfig('ak', 'sk', base_url='https://invalid',
                    retry_times=1, retry_interval_seconds=0, on_request=events.append)
        async def resolve(_path):
            return 'https://invalid/image'
        def respond(request):
            nonlocal queries
            if request.url.path == '/oauth/2.0/token':
                return httpx.Response(200, json={'access_token': 'private-token'})
            if request.url.path.endswith('/query'):
                queries += 1
                if queries == 1:
                    return httpx.Response(502, json={})
                return httpx.Response(200, json={'result': {'status': 'success', 'parse_result_url': 'https://invalid/result'}})
            if request.url.path == '/result':
                return httpx.Response(200, json={'pages': []})
            return httpx.Response(200, json={'result': {'task_id': 'private-task'}})
        runtime = VendorOCRRuntime(config, executor(), resolve)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            runtime._client = client
            collector = UsageCollector()
            await runtime.request(VendorOCRInput(1, Path('unused.png'), lambda: False), usage=collector)
        self.assertEqual([e.kind for e in events], [RequestKind.OCR_AUTH, RequestKind.OCR_SUBMIT,
                         RequestKind.OCR_QUERY, RequestKind.OCR_QUERY, RequestKind.OCR_DOWNLOAD])
        self.assertEqual([e.http_status for e in events], [200, 200, 502, 200, 200])
        self.assertEqual(events[2].outcome, RequestOutcome.SERVER_ERROR)
        self.assertTrue(all(e.usage is None for e in events))
        self.assertEqual(collector.report()[config].effective.requests, 1)

    def test_async_callbacks_are_rejected_at_configuration(self):
        async def callback(_event):
            pass
        with self.assertRaisesRegex(TypeError, 'synchronous'):
            llm(callback)

    async def test_waiting_for_capacity_is_not_a_request(self):
        events = []
        pool = ConcurrentExecutor(FixedCapacity(1))
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold():
            entered.set()
            await release.wait()
        holder = asyncio.create_task(pool.run(hold))
        await entered.wait()
        runtime = runtime_for(llm(events.append), pool)
        setattr(runtime, "_invoke", AsyncMock(return_value='answer'))
        waiting = asyncio.create_task(runtime.request('input'))
        await asyncio.sleep(0.02)
        self.assertEqual(events, [])
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual(events, [])
        released_at = datetime.now(timezone.utc)
        release.set()
        await holder
        await runtime.request('input')
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(events[0].started_at, released_at)

    async def test_total_deadline_reports_timeout_not_external_cancellation(self):
        events = []
        runtime = runtime_for(llm(events.append, total_timeout=0.02, retry_times=0), executor())
        async def invoke(*_args):
            await asyncio.Event().wait()
        setattr(runtime, "_invoke", invoke)
        with self.assertRaises(Exception):
            await runtime.request('input')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].outcome, RequestOutcome.TIMEOUT)

    async def test_received_504_is_server_error_not_client_timeout(self):
        events = []
        runtime = runtime_for(llm(events.append, retry_times=0), executor())
        response = httpx.Response(504, request=httpx.Request('POST', 'https://invalid'))
        setattr(runtime, "_invoke", AsyncMock(side_effect=openai.APIStatusError(
            'private error', response=response, body=None)))
        with self.assertRaises(Exception):
            await runtime.request('input')
        self.assertEqual(events[0].outcome, RequestOutcome.SERVER_ERROR)
        self.assertEqual(events[0].http_status, 504)

    async def test_jev_sdk_transport_preserves_status_without_changing_sdk_defaults(self):
        events = []
        async def respond(_transport, request):
            return httpx2.Response(200, request=request, json={
                'model': 'jev-latest', 'usage': {'input_tokens': 6, 'output_tokens': 1},
                'answers': {'page_passes_strict_standard': {'type': 'noul', 'noul': 0.8}},
            })
        with patch('pdf_craft.jev.httpx2.AsyncHTTPTransport.handle_async_request', respond):
            async with JEVRuntime(JEV('secret', on_request=events.append), executor()) as runtime:
                value = await runtime.evaluate(1, {'state': 'page', 'questions': {
                    'page_passes_strict_standard': {'type': 'noul', 'instructions': 'valid?'},
                }})
        self.assertEqual(value, 0.8)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].http_status, 200)
        self.assertEqual(events[0].outcome, RequestOutcome.SUCCESS)
        self.assertEqual(events[0].usage, RequestUsage(1, 6, 0, 1))

    async def test_nested_unobserved_provider_cannot_overwrite_outer_observation(self):
        events = []
        inner = runtime_for(llm(None), executor())
        async def inner_call(*_args):
            observe_tokens({'input_tokens': 99})
            observe_response(httpx.Response(201))
            return 'inner'
        setattr(inner, "_invoke", inner_call)
        outer = runtime_for(llm(events.append), executor())
        async def outer_call(*_args):
            observe_tokens({'input_tokens': 3})
            observe_response(httpx.Response(200))
            await inner.request('nested')
            return 'outer'
        setattr(outer, "_invoke", outer_call)
        await outer.request('input')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].usage.input_tokens, 3)
        self.assertEqual(events[0].http_status, 200)

    async def test_timeout_with_internal_cancel_cause_is_not_external_cancellation(self):
        events = []
        runtime = runtime_for(llm(events.append, retry_times=0), executor())
        error = httpx.ReadTimeout('timeout')
        error.__cause__ = asyncio.CancelledError()
        setattr(runtime, "_invoke", AsyncMock(side_effect=error))
        with self.assertRaises(Exception):
            await runtime.request('input')
        self.assertEqual(events[0].outcome, RequestOutcome.TIMEOUT)
