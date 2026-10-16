"""Regressions for the gaps left by PR 506; initial regressions written before the follow-up fix."""
# Subtests await every operation before advancing their loop variables.
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import httpx

from pdf_craft import AsyncPDFCraft, ConcurrentExecutor, ExtractionOptions, FixedCapacity, PDFCraft
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.generation import generate_chapter_files
from pdf_craft.extractor.toc.types import encode as encode_toc
from pdf_craft.metering import OCRTokensMetering
from tests.extraction_helpers import make_extraction
from pdf_craft.llm import LLM, runtime_for
from pdf_craft.llm.runtime import LLMRuntime, LLMTransportError
from pdf_craft.extractor.toc.analysing import analyse_toc
from pdf_craft.extractor.toc.llm_analyser import _LLMAnalyser, LLMAnalysisError


class DeadlineFollowupTests(unittest.IsolatedAsyncioTestCase):
    def test_default_budget_and_separate_http_timeout(self):
        config = LLM('key', 'https://example.invalid/v1', 'model', 'o200k_base')
        self.assertEqual(config.total_timeout, 360)
        self.assertIsNone(config.timeout)
        explicit = LLM('key', 'url', 'model', 'o200k_base', total_timeout=None)
        self.assertIsNone(explicit.total_timeout)
        for value in (0, -1, float('inf'), float('nan')):
            for field in ('total_timeout', 'cleanup_timeout'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    if field == 'total_timeout':
                        LLM('key', 'url', 'model', 'o200k_base', total_timeout=value)
                    else:
                        LLM('key', 'url', 'model', 'o200k_base', cleanup_timeout=value)

    async def test_http_timeout_does_not_consume_retry_budget(self):
        config = LLM('key', 'url', 'model', 'o200k_base', timeout=.01,
                     total_timeout=.3, retry_times=1, retry_interval_seconds=0)
        runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
        calls = 0

        async def invoke(*_args):
            nonlocal calls
            calls += 1
            await asyncio.sleep(.02)
            if calls == 1:
                raise httpx.ReadTimeout('one request timed out')
            return 'recovered'

        cast(Any, runtime)._invoke = invoke
        self.assertEqual(await runtime.request('hello', use_cache=False), 'recovered')
        self.assertEqual(calls, 2)

    async def test_slow_cleanup_is_bounded_and_capacity_is_reusable(self):
        """The old wait_for waited indefinitely for aclose during cancellation."""
        config = LLM('key', 'url', 'model', 'o200k_base', total_timeout=.03,
                     cleanup_timeout=.04, retry_times=0)
        executor = ConcurrentExecutor(FixedCapacity(1))
        runtime = runtime_for(config, executor)
        stream_stopped = asyncio.Event()
        client_closed = asyncio.Event()

        class Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(.005)
                return Mock(choices=[Mock(delta=Mock(content='x'))])

            async def aclose(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    stream_stopped.set()

        class Client:
            def __init__(self, **_kwargs):
                self.chat = Mock(completions=self)

            async def create(self, **_kwargs):
                return Stream()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                try:
                    await asyncio.Event().wait()
                finally:
                    client_closed.set()

        before = set(asyncio.all_tasks())
        started = time.monotonic()
        with patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', Client):
            with self.assertRaises(LLMTransportError):
                await asyncio.wait_for(runtime.request('hello', use_cache=False), .3)
        self.assertLess(time.monotonic() - started, .2)
        self.assertTrue(stream_stopped.is_set())
        self.assertTrue(client_closed.is_set())
        self.assertEqual(await asyncio.wait_for(executor.run(AsyncMock(return_value='ok')), .1), 'ok')
        self.assertFalse(set(asyncio.all_tasks()) - before)

    async def test_backoff_cancellation_releases_capacity(self):
        config = LLM('key', 'url', 'model', 'o200k_base', retry_times=2,
                     retry_interval_seconds=10)
        executor = ConcurrentExecutor(FixedCapacity(1))
        runtime = runtime_for(config, executor)
        failed = asyncio.Event()

        async def invoke(*_args):
            failed.set()
            raise httpx.ReadTimeout('retry')

        cast(Any, runtime)._invoke = invoke
        task = asyncio.create_task(runtime.request('hello', use_cache=False))
        await failed.wait()
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await asyncio.wait_for(executor.run(AsyncMock(return_value='ok')), .1), 'ok')

    async def test_schema_and_business_retries_share_transport_budget(self):
        for response in ('[]', '{"ok": 1}'):
            with self.subTest(response=response):
                config = LLM('key', 'url', 'model', 'o200k_base', total_timeout=.09,
                             retry_times=1, retry_interval_seconds=0)
                validations = []
                analyser = _LLMAnalyser(config, lambda value, _payload: (
                    validations.append(value) or None, 'business rejection'))
                calls = 0
                elapsed = 0.0

                async def invoke(*_args):
                    nonlocal calls, elapsed
                    calls += 1
                    elapsed += .04
                    await asyncio.sleep(0)
                    if calls % 2:
                        raise httpx.ReadTimeout('retryable')
                    return response

                cast(Any, analyser._runtime)._invoke = invoke
                clock = Mock(monotonic=lambda: elapsed)
                with patch('pdf_craft.llm.runtime.time', clock), \
                        patch('pdf_craft.extractor.toc.llm_analyser.time', clock), \
                        self.assertRaises(LLMAnalysisError):
                    await analyser.request(None, [])
                self.assertEqual(calls, 3)
                self.assertEqual(len(validations), 0 if response == '[]' else 1)

    async def test_toc_failures_fall_back_and_cancellation_propagates(self):
        for kind in ('timeout', 'transport', 'empty', 'schema', 'business', 'cancel'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pages = root / 'pages'
                pages.mkdir()
                (pages / 'page_1.xml').write_text(
                    "<page index='1'><body><layout ref='title' order='0' "
                    "det='1,1,90,30'>Chapter One</layout></body></page>", encoding='utf-8')
                config = LLM('key', 'url', 'model', 'o200k_base', total_timeout=.03,
                             retry_times=0)
                entered = asyncio.Event()
                stopped = asyncio.Event()

                async def invoke(*_args, **_kwargs):
                    entered.set()
                    try:
                        if kind in ('timeout', 'cancel'):
                            await asyncio.Event().wait()
                        if kind == 'transport':
                            raise httpx.ReadTimeout('provider')
                        return {'empty': '', 'schema': '[]', 'business': '{"0": 99}'}.get(kind, '')
                    finally:
                        stopped.set()

                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    task = asyncio.create_task(analyse_toc(pages, root / 'toc.xml', True, config))
                    if kind == 'cancel':
                        await entered.wait()
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                        self.assertFalse((root / 'toc.xml').exists())
                    else:
                        toc = await asyncio.wait_for(task, .3)
                        self.assertEqual(len(toc.content), 1)
                    self.assertTrue(stopped.is_set())


    async def test_epub_conversion_with_and_without_toc_llm(self):
        """Stub OCR only; exercise TOC analysis, chapter assembly and EPUB output."""
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = []

                class Engine:
                    async def extract_package_async(self, *, analysing_path, **kwargs):
                        pages = analysing_path / 'ocr'
                        pages.mkdir(parents=True)
                        (pages / 'page_1.xml').write_text(
                            "<page index='1'><body><layout ref='title' order='0' "
                            "det='1,1,90,30'>Chapter One</layout>"
                            "<layout ref='text' order='1' det='1,40,90,60'>Body.</layout>"
                            "</body></page>", encoding='utf-8')
                        extraction = analysing_path / 'extraction'
                        make_extraction(extraction, with_toc=True)
                        toc_path = extraction / 'toc.xml'
                        toc_path.unlink()
                        toc = await analyse_toc(pages, toc_path,
                                                kwargs['toc_assumed'], kwargs['toc_llm'])
                        save_xml(encode_toc(toc), toc_path)
                        generate_chapter_files(pages, extraction / 'chapters', toc)
                        return None, None, None, None, OCRTokensMetering(0, 0)

                async def invoke(*_args, **_kwargs):
                    calls.append('LLM')
                    await asyncio.Event().wait()

                config = LLM('key', 'url', 'model', 'o200k_base',
                             total_timeout=.03, retry_times=0)
                craft = AsyncPDFCraft.from_engine(Engine())
                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    await craft.convert_pdf_to_epub(
                        root / 'unused.pdf', root / 'book.epub',
                        extraction=ExtractionOptions(toc_assumed=enabled,
                                                     toc_llm=config if enabled else None))
                self.assertEqual(calls, ['LLM'] if enabled else [])
                self.assertTrue((root / 'book.epub').is_file())
                # The synchronous facade must use the same budget/fallback path.
                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    await asyncio.to_thread(
                        PDFCraft.from_engine(Engine()).convert_pdf_to_epub,
                        root / 'unused.pdf', root / 'sync.epub',
                        extraction=ExtractionOptions(toc_assumed=enabled,
                                                     toc_llm=config if enabled else None))
                self.assertEqual(calls, ['LLM', 'LLM'] if enabled else [])
                self.assertTrue((root / 'sync.epub').is_file())

    async def test_logs_include_retries_and_final_failure_in_both_sinks(self):
        for file_logs in (False, True):
            with self.subTest(file_logs=file_logs), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = LLM('key', 'url', 'model', 'o200k_base', retry_times=1,
                             retry_interval_seconds=0, log_dir_path=root if file_logs else None)
                runtime = runtime_for(config, ConcurrentExecutor(FixedCapacity(1)))
                cast(Any, runtime)._invoke = AsyncMock(side_effect=httpx.ReadTimeout('retry'))
                async def exercise():
                    with self.assertRaises(LLMTransportError):
                        await runtime.request('hello', use_cache=False)
                    cast(Any, runtime)._invoke = AsyncMock(return_value='ok')
                    self.assertEqual(await runtime.request('hello', use_cache=False), 'ok')
                    config.total_timeout = .02

                    async def block(*_args):
                        await asyncio.Event().wait()

                    cast(Any, runtime)._invoke = block
                    with self.assertRaises(LLMTransportError):
                        await asyncio.wait_for(runtime.request('hello', use_cache=False), .3)

                if file_logs:
                    await exercise()
                    output = ''.join(path.read_text() for path in root.glob('*.log'))
                else:
                    with self.assertLogs('pdf_craft.llm', level='INFO') as logs:
                        await exercise()
                    output = '\n'.join(logs.output)
                for category in ('request', 'transport-error', 'retry', 'failure', 'success', 'timeout'):
                    self.assertIn(f'"category": "{category}"', output)


    async def test_repeated_cancellation_does_not_detach_cleanup(self):
        config = LLM('key', 'url', 'model', 'o200k_base', cleanup_timeout=.04)
        executor = ConcurrentExecutor(FixedCapacity(1))
        runtime = runtime_for(config, executor)
        entered = asyncio.Event()
        cleanup_entered = asyncio.Event()
        stopped = asyncio.Event()

        class Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError('unreachable')

            async def aclose(self):
                cleanup_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        client = Mock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.chat.completions.create = AsyncMock(return_value=Stream())
        before = set(asyncio.all_tasks())
        with patch('pdf_craft.llm.runtime.openai.AsyncOpenAI', return_value=client):
            task = asyncio.create_task(runtime.request('hello', use_cache=False))
            await asyncio.wait_for(entered.wait(), .5)
            task.cancel()
            await asyncio.wait_for(cleanup_entered.wait(), .5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, .5)
        self.assertTrue(stopped.is_set())
        client.__aexit__.assert_awaited_once()
        self.assertFalse(set(asyncio.all_tasks()) - before)
        self.assertEqual(await asyncio.wait_for(executor.run(AsyncMock(return_value='ok')), .1), 'ok')
