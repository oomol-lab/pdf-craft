"""Resource accounting is independent of progress and survives rejected candidates."""
# pylint: disable=protected-access
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from pydantic import BaseModel

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity, JEV, LLM,
    NarrativeXMLTransformer, RequestUsage, RetryPolicy, SubmitKind, Usage, UsageCollector,
    epub_words_count, pcex_words_count,
)
from pdf_craft.jev import JEVRuntime
from pdf_craft.llm import runtime_for
from pdf_craft.llm.guaranteed import GuaranteedOptions, request_guaranteed_json
from pdf_craft.pdf.vendor_ocr import VendorOCRInput, VendorOCRRuntime
from pdf_craft.usage import UsageScope, observe_tokens, usage_context
from tests.test_one_shot_submission import source_fixture
from tests.test_stream_callbacks import epub
from tests.test_translation_word_progress import translator, identity_fill
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator


def model():
    return LLM('secret', 'https://invalid', 'model', 'o200k_base', retry_times=1, retry_interval_seconds=0)


def runtime(config):
    return runtime_for(config, ConcurrentExecutor(FixedCapacity(3)))


class UsageTests(IsolatedAsyncioTestCase):
    async def test_metadata_collection_usage_follows_final_adoption(self):
        for with_title in (False, True):
            for reject_subject in (False, True):
                with self.subTest(with_title=with_title, reject_subject=reject_subject):
                    translation, fill_llm = model(), model()
                    usage = UsageCollector()
                    xml = XMLTranslator(translation, fill_llm, 'fr', None, 10, 1,
                                        executor=ConcurrentExecutor(FixedCapacity(2)))
                    async def invoke_translation(*_args):
                        observe_tokens({'input_tokens': 10, 'output_tokens': 2,
                                        'cached_input_tokens': 4})
                        return 'translated'
                    async def invoke_fill(*_args):
                        observe_tokens({'input_tokens': 20, 'output_tokens': 3,
                                        'cached_input_tokens': 8})
                        return 'filled'
                    setattr(xml._translation_runtime, '_invoke', invoke_translation)
                    setattr(xml._fill_runtime, '_invoke', invoke_fill)
                    async def translate(transformer, text, *, reject=reject_subject):
                        await transformer._translation_runtime.request(text, use_cache=False)
                        if reject and 'bad' in text:
                            raise ValueError('subject translation rejected')
                        return text
                    async def fill(transformer, hill_climbing, **kwargs):
                        await transformer._fill_runtime.request('fill', use_cache=False)
                        await identity_fill(transformer, hill_climbing, **kwargs)
                    metadata: dict[str, Any] = {'subjects': ['good', 'bad']}
                    if with_title:
                        metadata['title'] = 'Title'
                    with patch.object(XMLTranslator, '_translate_text_async', translate), patch.object(
                            XMLTranslator, '_request_and_submit_async', fill):
                        overlay = await NarrativeXMLTransformer(xml).translate_metadata(
                            metadata, usage=usage, error_policy=lambda _failure: True)
                    if reject_subject:
                        self.assertNotIn('subjects', overlay)
                    else:
                        self.assertEqual(overlay['subjects'], ['good', 'bad'])
                    if with_title:
                        self.assertEqual(overlay['title'], 'Title')
                    accepted = int(with_title) + (0 if reject_subject else 2)
                    self.assertEqual(usage.report()[translation], Usage(
                        RequestUsage(accepted, accepted * 10, accepted * 4, accepted * 2),
                        RequestUsage(2, 20, 8, 4) if reject_subject else RequestUsage()))
                    self.assertEqual(usage.report()[fill_llm], Usage(
                        RequestUsage(accepted, accepted * 20, accepted * 8, accepted * 3),
                        RequestUsage(1, 20, 8, 3) if reject_subject else RequestUsage()))

    async def test_transport_retries_and_business_validation_settle_separately(self):
        llm, usage = model(), UsageCollector()
        client = runtime(llm)
        calls = 0
        async def invoke(*_args):
            nonlocal calls
            calls += 1
            observe_tokens({'prompt_tokens': 10, 'completion_tokens': 3,
                            'prompt_tokens_details': {'cached_tokens': 4}})
            if calls == 1:
                raise httpx.ReadError('connection broke after reporting usage')
            return '{"value": -1}' if calls == 2 else '{"value": 1}'
        setattr(client, "_invoke", invoke)
        class Answer(BaseModel):
            value: int
        def validate(data, _index, _maximum):
            if data.value < 0:
                raise ValueError('candidate rejected')
            return data.value
        with usage_context(usage):
            answer = await request_guaranteed_json(GuaranteedOptions(
                messages=[], request=lambda messages, _i, _m: client.request(messages), schema=Answer,
                parse=validate, retry_policy=RetryPolicy(analysis=1)))
        self.assertEqual(answer, 1)
        self.assertEqual(usage.report()[llm], Usage(RequestUsage(1, 10, 4, 3), RequestUsage(2, 20, 8, 6)))

    async def test_real_stream_usage_and_provider_cache_are_observed(self):
        llm, usage = model(), UsageCollector()
        stream = MagicMock()
        stream.__aiter__.return_value = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='answer'))], usage=None),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=20,
                prompt_tokens_details=SimpleNamespace(cached_tokens=12))),
            SimpleNamespace(choices=[], usage=SimpleNamespace(completion_tokens=5)),
        ]
        stream.aclose = AsyncMock()
        sdk = MagicMock()
        sdk.__aenter__ = AsyncMock(return_value=sdk)
        sdk.__aexit__ = AsyncMock()
        sdk.chat.completions.create = AsyncMock(return_value=stream)
        with patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', return_value=sdk):
            self.assertEqual(await runtime(llm).request('source', usage=usage), 'answer')
        self.assertEqual(usage.report()[llm].effective, RequestUsage(1, 20, 12, 5))
        self.assertEqual(sdk.chat.completions.create.call_args.kwargs['stream_options'], {'include_usage': True})

    async def test_missing_usage_requests_identity_snapshots_and_local_cache(self):
        first, second = model(), model()
        usage = UsageCollector()
        with TemporaryDirectory() as directory:
            first.cache_path = Path(directory)
            client = runtime(first)
            setattr(client, "_invoke", AsyncMock(return_value='answer'))
            await client.request('input', usage=usage)
            await client.request('input', usage=usage)
            other = runtime(second)
            setattr(other, "_invoke", AsyncMock(return_value='answer'))
            await other.request('input', usage=usage)
        report = usage.report()
        self.assertEqual(len(report), 2)
        self.assertEqual(report[first].effective, RequestUsage(requests=1))
        self.assertEqual(report[second].effective, RequestUsage(requests=1))
        report[first].effective.requests = 100
        self.assertEqual(usage.report()[first].effective.requests, 1)

    async def test_concurrent_tasks_and_cancellation_do_not_share_collectors(self):
        llm, first, second = model(), UsageCollector(), UsageCollector()
        client = runtime(llm)
        entered, release = asyncio.Event(), asyncio.Event()
        async def invoke(messages, *_args):
            observe_tokens({'input_tokens': 9, 'output_tokens': 2})
            if messages[0].message == 'cancel':
                entered.set()
                await release.wait()
            return 'answer'
        setattr(client, "_invoke", invoke)
        pending = asyncio.create_task(client.request('cancel', usage=first))
        await entered.wait()
        await client.request('complete', usage=second)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(first.report()[llm], Usage(wasted=RequestUsage(1, 9, 0, 2)))
        self.assertEqual(second.report()[llm], Usage(effective=RequestUsage(1, 9, 0, 2)))

    async def test_later_candidate_rejection_discards_normal_multiround_calls(self):
        llm, usage = model(), UsageCollector()
        client = runtime(llm)
        async def invoke(*_args):
            observe_tokens({'input_tokens': 5, 'output_tokens': 1})
            return 'answer'
        setattr(client, "_invoke", invoke)
        with usage_context(usage):
            with UsageScope():  # rejected by adapter after its inner calls succeeded
                await client.request('translate')
                await client.request('fill')
            with UsageScope() as accepted:
                await client.request('normal round one')
                await client.request('normal round two')
                accepted.accept()
        self.assertEqual(usage.report()[llm], Usage(RequestUsage(2, 10, 0, 2), RequestUsage(2, 10, 0, 2)))

    async def test_jev_low_probability_is_effective_and_invalid_probability_is_wasted(self):
        jev, usage = JEV('secret'), UsageCollector()
        sdk = MagicMock()
        sdk.__aenter__ = AsyncMock(return_value=sdk)
        sdk.__aexit__ = AsyncMock()
        sdk.system_one = AsyncMock(side_effect=[SimpleNamespace(
            usage=SimpleNamespace(input_tokens=40, output_tokens=1),
            nouls={'page_passes_strict_standard': SimpleNamespace(noul=value)}) for value in (0.1, 2)])
        with patch('pdf_craft.jev.AsyncTypeSafeClient', return_value=sdk):
            async with JEVRuntime(jev, ConcurrentExecutor(FixedCapacity(1))) as client:
                self.assertEqual(await client.evaluate(1, {'state': {}, 'questions': {}}, usage=usage), 0.1)
                with self.assertRaises(Exception):
                    await client.evaluate(2, {'state': {}, 'questions': {}}, usage=usage)
        self.assertEqual(usage.report()[jev], Usage(RequestUsage(1, 40, 0, 1), RequestUsage(1, 40, 0, 1)))

    async def test_ocr_usage_survives_invalid_content_and_raw_decode_rejection(self):
        config = DeepSeekOCRVendorConfig('https://invalid', 'key', 'ocr', retry_times=0)
        usage = UsageCollector()
        async def resolve(_path):
            return 'https://images.invalid/page.png'
        responses = iter([
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}},
            {'choices': [{'message': {'content': 'invalid OCR layout'}}],
             'usage': {'prompt_tokens': 20, 'completion_tokens': 4, 'prompt_cache_hit_tokens': 8}},
        ])
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=next(responses), request=request))) as transport:
            client = VendorOCRRuntime(config, ConcurrentExecutor(FixedCapacity(1)), resolve)
            client._client = transport
            with self.assertRaises(Exception):
                await client.request(VendorOCRInput(1, Path('unused'), lambda: False), usage=usage)
            # High-level OCR owns final layout validation; an unread candidate is discarded at operation exit.
            with usage_context(usage):
                response = await client.request(VendorOCRInput(2, Path('unused'), lambda: False), _defer_usage=True)
                self.assertIsNotNone(response._usage)
        self.assertEqual(usage.report()[config].wasted, RequestUsage(2, 30, 8, 6))
        self.assertEqual(usage.report()[config].effective.requests, 0)

    async def test_unlimited_submissions_count_without_polling_and_resubmits_are_wasted(self):
        from pdf_craft import UnlimitedOCRVendorConfig
        config = UnlimitedOCRVendorConfig(ak='ak', sk='sk', poll_interval_seconds=0,
                                           retry_times=1, retry_interval_seconds=0)
        submits, polls = 0, 0
        async def resolve(_path):
            return 'https://images.invalid/page.png'
        def handler(request):
            nonlocal submits, polls
            if request.url.path.endswith('/task'):
                submits += 1
                return httpx.Response(200, json={'result': {'task_id': f'task-{submits}'}}, request=request)
            if request.url.path.endswith('/query'):
                polls += 1
                if submits == 1:
                    result = {'status': 'failed', 'task_error': '任务失败'}
                elif polls < 4:
                    result = {'status': 'running'}
                else:
                    result = {'status': 'success', 'parse_result_url': 'https://invalid/result'}
                return httpx.Response(200, json={'result': result}, request=request)
            return httpx.Response(200, json={}, request=request)
        usage = UsageCollector()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = VendorOCRRuntime(config, ConcurrentExecutor(FixedCapacity(2)), resolve)
            client._client, client._access_token = transport, 'token'
            await client.request(VendorOCRInput(1, Path('unused.png'), lambda: False), usage=usage)
        self.assertEqual((submits, polls), (2, 4))
        self.assertEqual(usage.report()[config], Usage(RequestUsage(requests=1), RequestUsage(requests=1)))

    async def test_ocr_first_adopted_stage_survives_second_stage_failure(self):
        from PIL import Image
        from pdf_craft.pdf.ocr import OCR, _PreparedVendorPage
        from pdf_craft.pdf import ocr as ocr_module
        config = DeepSeekOCRVendorConfig('https://invalid', 'key', 'ocr', retry_times=0)
        usage = UsageCollector()
        sent = 0
        def handler(request):
            nonlocal sent
            sent += 1
            payload: dict[str, Any] = {'usage': {'prompt_tokens': 20, 'completion_tokens': 4}}
            payload['choices'] = [{'message': {'content': 'layout'}}] if sent == 1 else []
            return httpx.Response(200, json=payload, request=request)
        async def resolve(_path):
            return 'https://images.invalid/page.png'
        with TemporaryDirectory() as directory:
            image = Path(directory) / 'page.png'
            with Image.new('RGB', (4, 4), 'white') as rendered:
                # Both mocked requests require visible input, including pass 2.
                rendered.putpixel((3, 3), (0, 0, 0))
                rendered.save(image)
            client = VendorOCRRuntime(config, ConcurrentExecutor(FixedCapacity(1)), resolve)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
                client._client = transport
                with usage_context(usage), patch.object(ocr_module, '_decode_vendor_response', return_value=SimpleNamespace()), patch.object(
                        ocr_module, '_prepare_second_stage', return_value=_PreparedVendorPage(1, image, image, 0, 1)):
                    with self.assertRaises(Exception):
                        await OCR(config, None)._recognize_vendor_page(client,
                            _PreparedVendorPage(1, image, image, 0, 1), asset_hub=cast(Any, None),
                            includes_footnotes=True, plot_path=None, aborted=lambda: False)
        self.assertEqual(sent, 2)
        self.assertEqual(usage.report()[config], Usage(RequestUsage(1, 20, 0, 4), RequestUsage(1, 20, 0, 4)))

    async def test_facade_carries_usage_through_translation_and_fill(self):
        from pdf_craft.llm.runtime import LLMRuntime
        llm, usage = model(), UsageCollector()
        executor = ConcurrentExecutor(FixedCapacity(3))
        async def invoke(_self, messages, *_args, **_kwargs):
            observe_tokens({'prompt_tokens': 10, 'completion_tokens': 2})
            return messages[-1].message
        async def fill(transformer, hill_climbing, **_kwargs):
            await transformer._fill_runtime.request('fill', use_cache=False)
            error = hill_climbing.submit(hill_climbing.request_element())
            if error:
                raise AssertionError(error)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_fixture(root / 'source')
            xml_translator = XMLTranslator(llm, llm, 'fr', None, 10, 30, executor=executor)
            with patch.object(LLMRuntime, '_invoke_for_request', invoke), patch.object(
                    XMLTranslator, '_request_and_submit_async', fill):
                await AsyncPDFCraft().translate_extraction(source, root / 'target.pcex', xml_translator,
                    target_language='fr', usage=usage, window=3)
        result = usage.report()[llm]
        self.assertGreater(result.effective.requests, 1)
        self.assertEqual(result.effective.input_tokens, result.effective.requests * 10)
        self.assertEqual(result.wasted.requests, 0)

    def test_sync_facade_forwards_collector_and_size_options(self):
        from pdf_craft import PDFCraft
        usage = UsageCollector()
        craft = PDFCraft()
        with patch.object(craft._async_craft, 'translate_epub', new=AsyncMock()) as call:
            craft.translate_epub('source.epub', 'target.epub', target_language='fr',
                                 submit=SubmitKind.REPLACE, usage=usage)
        self.assertIs(call.call_args.kwargs['usage'], usage)
        with patch.object(craft._async_craft, 'pcex_words_count', new=AsyncMock(return_value=42)) as count:
            self.assertEqual(craft.pcex_words_count('book.pcex', with_furniture=True), 42)
        self.assertTrue(count.call_args.kwargs['with_furniture'])


class SizeTests(IsolatedAsyncioTestCase):
    async def test_pcex_preview_matches_progress_for_all_options(self):
        async def identity(_self, text):
            return text
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_fixture(root / 'source')
            for anchored in (False, True):
                for furniture in (False, True):
                    events = []
                    expected = pcex_words_count(source, with_anchored=anchored, with_furniture=furniture)
                    self.assertEqual(await AsyncPDFCraft().pcex_words_count(source,
                        with_anchored=anchored, with_furniture=furniture), expected)
                    with patch.object(XMLTranslator, '_translate_text_async', identity), patch.object(
                            XMLTranslator, '_request_and_submit_async', identity_fill):
                        await AsyncPDFCraft().translate_extraction(source, root / f'{anchored}-{furniture}.pcex',
                            translator(), with_anchored=anchored, with_furniture=furniture,
                            target_language='fr', on_translation_event=events.append)
                    self.assertEqual(events[0].total_words, expected)

    async def test_epub_preview_matches_progress_without_output_or_model(self):
        async def identity(_self, text):
            return text
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.epub'
            epub(source)
            expected = epub_words_count(source)
            self.assertGreater(expected, 0)
            self.assertEqual(list(root.iterdir()), [source])
            self.assertEqual(await AsyncPDFCraft().epub_words_count(source), expected)
            events = []
            with patch.object(XMLTranslator, '_translate_text_async', identity), patch.object(
                    XMLTranslator, '_request_and_submit_async', identity_fill):
                await AsyncPDFCraft().translate_epub(source, root / 'target.epub', target_language='fr',
                    llm=model(), executor=ConcurrentExecutor(FixedCapacity(2)),
                    submit=SubmitKind.REPLACE, on_translation_event=events.append)
            self.assertEqual(events[0].total_words, expected)
