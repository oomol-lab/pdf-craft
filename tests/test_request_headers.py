"""Operation headers reach real HTTP request builders without shared state."""
# pylint: disable=protected-access,unused-argument,cell-var-from-loop
import asyncio
import inspect
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import httpx2
import openai
from PIL import Image
from typesafe_sdk import AsyncTypeSafeClient

from pdf_craft import (
    AsyncPDFCraft, PDFCraft, ConcurrentExecutor, FixedCapacity, LLM,
    DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig, UnlimitedOCRVendorConfig,
    JEV, RetryPolicy, XMLTranslator,
)
from pdf_craft._policy import policy_operation
from pdf_craft._request_headers import current_headers
from pdf_craft.jev import JEVRuntime
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime
from pdf_craft.runtime import IO_DOMAIN


@policy_operation
async def operation(callback, *, request_headers=None):
    return await callback()


def executor():
    return ConcurrentExecutor(FixedCapacity(4))


def llm():
    return LLMRuntime(LLM('key', 'https://model.invalid/v1', 'model', 'o200k_base',
                          retry_times=1, retry_interval_seconds=0), executor=executor())


def stream_response(request):
    return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, text=(
        'data: {"id":"1","object":"chat.completion.chunk","created":0,"model":"model",'
        '"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
        'data: [DONE]\n\n'
    ))


def openai_client(handler):
    original = openai.AsyncOpenAI

    def client(**kwargs):
        kwargs['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return original(**kwargs)

    return patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', client)


class RequestHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_inheritance_replacement_and_clear(self):
        async def capture():
            return current_headers()

        async def outer():
            self.assertEqual(await operation(capture), {'X-Test': 'outer'})
            self.assertEqual(await operation(capture, request_headers={'X-Other': 'inner'}),
                             {'X-Other': 'inner'})
            self.assertEqual(await operation(capture, request_headers={}), {})
            self.assertEqual(current_headers(), {'X-Test': 'outer'})

        await operation(outer, request_headers={'X-Test': 'outer'})
        self.assertEqual(current_headers(), {})

    async def test_snapshot_thread_propagation_and_cancel_cleanup(self):
        supplied = {'X-Test': 'original'}
        entered, release = asyncio.Event(), asyncio.Event()

        async def body():
            entered.set()
            await release.wait()
            return await IO_DOMAIN.run(current_headers)

        task = asyncio.create_task(operation(body, request_headers=supplied))
        await entered.wait()
        supplied['X-Test'] = 'mutated'
        release.set()
        self.assertEqual(await task, {'X-Test': 'original'})
        self.assertEqual(current_headers(), {})

        async def cancel():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await operation(cancel, request_headers={'X-Test': 'cancel'})
        self.assertEqual(current_headers(), {})

    async def test_validation_precedes_work_and_protects_protocol_headers(self):
        callback = AsyncMock()
        for headers in ({'AUTHORIZATION': 'bad'}, {'Content-Type': 'bad'}, {'Host': 'bad'},
                        {'Content-Length': '1'}, {'Transfer-Encoding': 'chunked'},
                        {'X-Bad\n': 'bad'}, {'X-Test': 'bad\r\nvalue'},
                        {'X-Test': 'one', 'x-test': 'two'}):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                await operation(callback, request_headers=headers)
        callback.assert_not_awaited()
        self.assertEqual(current_headers(), {})

    async def test_shared_llm_and_executor_keep_headers_on_retries(self):
        runtime = llm()
        seen, counts = [], Counter()

        async def respond(request):
            value = request.headers.get('X-Test')
            seen.append((value, request.headers['Authorization'], request.headers['Content-Type']))
            counts[value] += 1
            await asyncio.sleep(0)
            if counts[value] == 1:
                return httpx.Response(500, json={'error': {'message': 'retry'}})
            return stream_response(request)

        with openai_client(respond):
            self.assertEqual(await asyncio.gather(*(
                runtime.request('text', use_cache=False, request_headers={'X-Test': value})
                for value in ('one', 'two')
            )), ['ok', 'ok'])
        self.assertEqual(counts, {'one': 2, 'two': 2})
        self.assertTrue(all(auth == 'Bearer key' and content.startswith('application/json')
                            for _, auth, content in seen))
        self.assertEqual(current_headers(), {})

    async def test_shared_craft_sets_scope_for_nested_requests_and_restores_on_error(self):
        runtime, seen = llm(), []

        def respond(request):
            seen.append(request.headers.get('X-Test'))
            return stream_response(request)

        async def extract(**kwargs):
            self.assertEqual(await runtime.request('text', use_cache=False), 'ok')
            raise LookupError('stop before OCR/output')

        engine = Mock(spec=['extract_package_async'])
        engine.extract_package_async = AsyncMock(side_effect=extract)
        craft = AsyncPDFCraft.from_engine(engine)
        with tempfile.TemporaryDirectory() as folder, openai_client(respond):
            async def extract_one(value):
                with self.assertRaisesRegex(LookupError, 'stop before'):
                    await craft.extract_pdf(Path(folder) / 'source.pdf', Path(folder) / 'out.pcex',
                                            request_headers={'X-Test': value})
                self.assertEqual(current_headers(), {})

            await asyncio.gather(extract_one('one'), extract_one('two'))
        self.assertEqual(sorted(seen), ['one', 'two'])

    async def test_shared_translator_public_boundary_inherits_to_llm(self):
        config = LLM('key', 'https://model.invalid/v1', 'model', 'o200k_base')
        translator = XMLTranslator(config, config, 'en', None, 3, 1000, executor=executor())
        seen = []

        def respond(request):
            seen.append(request.headers.get('X-Test'))
            return stream_response(request)

        async def translate(**kwargs):
            await translator._translation_runtime.request('text', use_cache=False)
            return []

        with openai_client(respond), patch.object(translator, '_translate_elements', side_effect=translate):
            await asyncio.gather(*(
                translator.translate_elements([], request_headers={'X-Test': value})
                for value in ('one', 'two')
            ))
        self.assertEqual(sorted(seen), ['one', 'two'])
        self.assertEqual(current_headers(), {})

    async def test_both_deepseek_backends_shared_runtime_and_batch_scope(self):
        for config_type in (DeepSeekOCRVendorConfig, DeepSeekOCR2VendorConfig):
            seen, counts = [], Counter()

            async def respond(request):
                value = request.headers.get('X-Test')
                counts[value] += 1
                seen.append((value, request.headers['Authorization'], request.headers['Content-Type']))
                await asyncio.sleep(0)
                if counts[value] == 1:
                    return httpx.Response(500, json={'error': 'retry'})
                return httpx.Response(200, json={
                    'choices': [{'message': {'content': 'ok'}}],
                    'usage': {'prompt_tokens': 1, 'completion_tokens': 1},
                })

            async def resolve(path):
                return 'https://images.invalid/page.png'

            runtime = VendorOCRRuntime(config_type('https://model.invalid/v1', 'key', 'model',
                                                   retry_interval_seconds=0), executor(), resolve)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                runtime._client = client
                await asyncio.gather(*(
                    runtime.request(VendorOCRInput(1, Path('unused'), lambda: False),
                                    request_headers={'X-Test': value})
                    for value in ('one', 'two')
                ))
                async for result in runtime.request_many(
                    [VendorOCRInput(1, Path('unused'), lambda: False)],
                    request_headers={'X-Test': 'batch'},
                ):
                    self.assertIsNone(result.error)
                    # An async iterator must not leak its scope into its consumer.
                    self.assertEqual(current_headers(), {})
            self.assertEqual(counts, {'one': 2, 'two': 2, 'batch': 2})
            self.assertTrue(all(auth == 'Bearer key' and content == 'application/json'
                                for _, auth, content in seen))

    async def test_unlimited_token_submit_query_retry_but_not_result_download(self):
        seen, counts = [], Counter()

        def respond(request):
            path = request.url.path
            seen.append((request.url.host, path, request.headers.get('X-Test')))
            counts[path] += 1
            if path.endswith('/token'):
                return httpx.Response(200, json={'access_token': 'token'})
            if path.endswith('/task/query'):
                if counts[path] == 1:
                    return httpx.Response(500, json={'error': 'retry'})
                return httpx.Response(200, json={'result': {
                    'status': 'success', 'parse_result_url': 'https://download.invalid/result',
                }})
            if path.endswith('/task'):
                return httpx.Response(200, json={'result': {'task_id': 'task'}})
            return httpx.Response(200, json={'pages': []})

        runtime = VendorOCRRuntime(UnlimitedOCRVendorConfig(
            ak='ak', sk='sk', base_url='https://model.invalid',
            retry_times=1, retry_interval_seconds=0, poll_interval_seconds=0,
        ), executor())
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'page.png'
            Image.new('RGB', (2, 2)).save(image)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                runtime._client = client
                await runtime.request(VendorOCRInput(1, image, lambda: False),
                                      request_headers={'X-Test': 'scope'})
        self.assertTrue(all(value == 'scope' for host, _, value in seen if host == 'model.invalid'))
        self.assertEqual([value for host, _, value in seen if host == 'download.invalid'], [None])
        self.assertEqual(counts['/rest/2.0/brain/online/v2/unlimited-ocr-parser/task/query'], 2)

    async def test_jev_real_client_shared_instance_and_retry(self):
        seen, counts = [], Counter()

        async def respond(request):
            value = request.headers.get('X-Test')
            counts[value] += 1
            seen.append((value, request.headers.get('Authorization')))
            await asyncio.sleep(0)
            if counts[value] == 1:
                return httpx2.Response(500, json={'error': {'message': 'retry'}})
            return httpx2.Response(200, json={
                'model': 'model', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                'answers': {'page_passes_strict_standard': {'type': 'noul', 'noul': 0.9}},
            })

        async with AsyncTypeSafeClient(api_key='key', model='model', base_url='https://model.invalid',
                                       transport=httpx2.MockTransport(respond)) as client:
            runtime = JEVRuntime(JEV('key'), executor(), retry_policy=RetryPolicy(analysis=1))
            runtime._client = client
            request = {'state': 'page', 'questions': {'page_passes_strict_standard': {'type': 'noul'}}}
            self.assertEqual(await asyncio.gather(*(
                runtime.evaluate(1, request, request_headers={'X-Test': value})
                for value in ('one', 'two')
            )), [0.9, 0.9])
        self.assertEqual(counts, {'one': 2, 'two': 2})
        self.assertTrue(all(auth == 'Bearer key' for _, auth in seen))


class SynchronousHeaderTests(unittest.TestCase):
    def test_sync_facade_forwards_and_does_not_retain_headers(self):
        async def capture(*args, **kwargs):
            return kwargs['request_headers']

        craft = PDFCraft()
        with patch.object(craft._async_craft, 'extract_pdf', side_effect=capture):
            self.assertEqual(craft.extract_pdf('a.pdf', 'a.pcex', request_headers={'X-Test': 'one'}),
                             {'X-Test': 'one'})
        self.assertEqual(current_headers(), {})

    def test_all_craft_operations_match_sync_keyword_parameter(self):
        for name, method in inspect.getmembers(AsyncPDFCraft, inspect.isfunction):
            if 'request_headers' in inspect.signature(method).parameters:
                for facade in (AsyncPDFCraft, PDFCraft):
                    parameter = inspect.signature(getattr(facade, name)).parameters['request_headers']
                    self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
                    self.assertIsNone(parameter.default)
