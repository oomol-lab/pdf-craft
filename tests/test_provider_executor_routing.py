"""Offline coverage of caller-owned provider admission through extraction APIs."""
# pylint: disable=protected-access,cell-var-from-loop

import asyncio
from contextvars import ContextVar
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import openai

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, ExtractionOptions, FixedCapacity,
    JEV, LLM, PDFCraft, Refinement, RetryPolicy,
)
from pdf_craft.extractor.metadata import extract_book_metadata_from_ocr
from pdf_craft.extractor.toc.analysing import analyse_toc
from pdf_craft.llm.runtime import LLMRuntime, LLMTransportError
from pdf_craft.extractor.toc.toc_pages import MatchedTitle, PageRef, TitleReference
from pdf_craft.extractor.toc.llm_analyser import LLMAnalysisError
from pdf_craft.transformer import XMLTranslator
from tests.test_craft import _Engine
from tests.test_toc_identity import IdentityOCR, RefinedEngine


_OWNER: ContextVar['_ObservedExecutor | None'] = ContextVar('provider_executor_owner', default=None)


class _ObservedExecutor(ConcurrentExecutor):
    def __init__(self, *, recover=False):
        super().__init__(FixedCapacity(1))
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.recovered = 0
        self.recover = recover

    def __bool__(self):
        # Protocol implementations need not have a true boolean value.
        return False

    async def run(self, operation):
        self.calls += 1
        async def observe():
            self.active += 1
            self.peak = max(self.peak, self.active)
            token = _OWNER.set(self)
            try:
                try:
                    return await operation()
                except Exception:  # caller-defined, bounded recovery wrapper
                    if not self.recover:
                        raise
                    self.recovered += 1
                    return await operation()
            finally:
                _OWNER.reset(token)
                self.active -= 1
        return await super().run(observe)


class ProviderExecutorRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pages = self.root / 'pages'
        self.pages.mkdir()
        (self.pages / 'page_1.xml').write_text(
            "<page index='1'><body><layout ref='title' order='0' "
            "det='1,1,90,30'>Chapter One</layout></body></page>", encoding='utf-8')
        self.llm = LLM('key', 'https://example.invalid/v1', 'model', 'o200k_base',
                       retry_times=1, retry_interval_seconds=0)

    async def title(self, executor, **kwargs):
        return await analyse_toc(self.pages, self.root / 'toc.xml', False, self.llm,
                                 toc_executor=executor, **kwargs)

    async def metadata(self, executor):
        return await extract_book_metadata_from_ocr(self.pages, self.llm, executor=executor)

    async def test_shared_capacity_and_custom_recovery_cover_both_requests(self):
        executor = _ObservedExecutor(recover=True)
        seen = []
        failed = False
        async def invoke(runtime, *_args, **_kwargs):
            nonlocal failed
            self.assertIs(_OWNER.get(), executor)
            seen.append(runtime.protocol_version)
            await asyncio.sleep(0.001)
            if not failed:
                failed = True
                raise httpx.ReadError('synthetic transient error')
            if runtime.protocol_version == 'toc-json-v1':
                return '{"0":0}'
            return '{"action":"complete","metadata":{}}'
        with patch.object(LLMRuntime, '_invoke_async', invoke):
            toc, _ = await asyncio.wait_for(asyncio.gather(
                self.title(executor), self.metadata(executor)), 3)
        self.assertEqual(len(toc.content), 1)
        self.assertEqual(executor.calls, 2)
        self.assertEqual(executor.recovered, 1)
        self.assertEqual(executor.peak, 1)
        self.assertEqual(set(seen), {'toc-json-v1', 'book-metadata-json-v1'})

    async def test_protocol_retries_reenter_each_selected_executor(self):
        title_executor, metadata_executor = _ObservedExecutor(), _ObservedExecutor()
        counts = {}
        async def invoke(runtime, *_args, **_kwargs):
            role = runtime.protocol_version
            expected = title_executor if role == 'toc-json-v1' else metadata_executor
            self.assertIs(_OWNER.get(), expected)
            counts[role] = counts.get(role, 0) + 1
            if counts[role] == 1:
                return '{}'
            return '{"0":0}' if role == 'toc-json-v1' else '{"action":"complete","metadata":{}}'
        with patch.object(LLMRuntime, '_invoke_async', invoke):
            await asyncio.wait_for(asyncio.gather(
                self.title(title_executor), self.metadata(metadata_executor)), 3)
        self.assertEqual(title_executor.calls, 2)
        self.assertEqual(metadata_executor.calls, 2)

    async def test_cancellation_releases_shared_capacity(self):
        for role in ('title', 'metadata'):
            with self.subTest(role=role):
                executor = _ObservedExecutor()
                entered = asyncio.Event()
                async def invoke(*_args, **_kwargs):
                    self.assertIs(_OWNER.get(), executor)
                    entered.set()
                    await asyncio.Event().wait()
                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    task = asyncio.create_task(getattr(self, role)(executor))
                    await asyncio.wait_for(entered.wait(), 3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual(executor.active, 0)
                self.assertEqual(await asyncio.wait_for(executor.run(
                    lambda: asyncio.sleep(0, result='released')), 1), 'released')

    async def test_public_facade_forwards_independent_extraction_executors(self):
        for synchronous in (False, True):
            with self.subTest(synchronous=synchronous):
                toc, metadata = _ObservedExecutor(), _ObservedExecutor()
                options = ExtractionOptions(toc_executor=toc, metadata_executor=metadata)
                engine = _Engine()
                target = self.root / f'{synchronous}.pcex'
                if synchronous:
                    await asyncio.to_thread(PDFCraft.from_engine(engine).extract_pdf,
                                            'source.pdf', target, options)
                else:
                    await AsyncPDFCraft.from_engine(engine).extract_pdf('source.pdf', target, options)
                assert engine.kwargs is not None
                self.assertIs(engine.kwargs['toc_executor'], toc)
                self.assertIs(engine.kwargs['metadata_executor'], metadata)

    async def test_real_engine_consumes_options_in_async_sync_and_one_shot_paths(self):
        for entry in ('async', 'sync', 'markdown'):
            with self.subTest(entry=entry):
                toc, metadata = _ObservedExecutor(), _ObservedExecutor()
                engine = RefinedEngine('identity')
                options = ExtractionOptions(includes_furniture=False,
                    extract_book_metadata=True, metadata_llm=self.llm, toc_llm=self.llm,
                    toc_executor=toc, metadata_executor=metadata)
                seen = []
                async def invoke(runtime, *_args, **_kwargs):
                    role = runtime.protocol_version
                    self.assertIs(_OWNER.get(), toc if role == 'toc-json-v1' else metadata)
                    seen.append(role)
                    return ('{"0":0,"1":0}' if role == 'toc-json-v1'
                            else '{"action":"complete","metadata":{}}')
                with patch.object(LLMRuntime, '_invoke_async', invoke), patch.object(
                        engine, '_read_pdf_metadata_async', AsyncMock(return_value=None)), patch.object(
                        IdentityOCR, '_get_pdf_handler', return_value=None, create=True), patch(
                        'pdf_craft.pdf.page_ref.pdf_pages_count', return_value=6):
                    craft = AsyncPDFCraft.from_engine(engine)
                    if entry == 'sync':
                        await asyncio.to_thread(PDFCraft.from_engine(engine).extract_pdf,
                            'source.pdf', self.root / 'sync-real.pcex', options)
                    elif entry == 'markdown':
                        await craft.convert_pdf_to_markdown('source.pdf', self.root / 'out.md',
                                                            extraction=options)
                    else:
                        await craft.extract_pdf('source.pdf', self.root / 'async-real.pcex', options)
                self.assertEqual(sorted(seen), ['book-metadata-json-v1', 'toc-json-v1'])
                self.assertEqual((toc.calls, metadata.calls), (1, 1))

    async def test_transport_retries_and_bounded_fallback_use_selected_executor(self):
        for role in ('title', 'metadata'):
            with self.subTest(role=role):
                executor = _ObservedExecutor()
                async def invoke(*_args, **_kwargs):
                    self.assertIs(_OWNER.get(), executor)
                    raise openai.InternalServerError('synthetic busy provider',
                        response=httpx.Response(503, request=httpx.Request('POST', self.llm.url)),
                        body=None)
                with patch.object(LLMRuntime, '_invoke_async', invoke):
                    if role == 'title':
                        with self.assertRaises(LLMAnalysisError):
                            await self.title(executor, retry_policy=RetryPolicy(analysis=0))
                        toc = await self.title(executor, error_policy=True,
                                               retry_policy=RetryPolicy(analysis=0))
                        self.assertEqual(len(toc.content), 1)
                    else:
                        with self.assertRaises(LLMTransportError):
                            await self.metadata(executor)
                        engine = RefinedEngine('identity')
                        with patch.object(engine, '_read_pdf_metadata_async', AsyncMock(return_value=None)):
                            metadata = await engine._extract_book_metadata_async(
                                pdf_path=self.root / 'source.pdf', pages_path=self.pages,
                                enabled=True, metadata_llm=self.llm, metadata_executor=executor,
                                error_policy=True)
                        assert metadata is not None
                        self.assertIsNone(metadata.title)
                self.assertEqual(executor.calls, 4)  # Two bounded calls, two attempts each.
                self.assertEqual(executor.active, 0)

    async def test_printed_toc_and_metadata_read_more_use_selected_executors(self):
        toc, metadata = _ObservedExecutor(), _ObservedExecutor()
        for index in range(2, 6):
            (self.pages / f'page_{index}.xml').write_text(
                f'<page index="{index}"><body><layout ref="text" '
                'det="1,1,90,30">Chapter One</layout></body></page>', encoding='utf-8')
        reference = PageRef(2, 1.0, [MatchedTitle('Chapter One', 1.0, [TitleReference(1, 0)])])
        turns = 0
        async def invoke(runtime, *_args, **_kwargs):
            nonlocal turns
            if runtime.protocol_version == 'toc-json-v1':
                self.assertIs(_OWNER.get(), toc)
                return '{"A":0}'
            self.assertIs(_OWNER.get(), metadata)
            turns += 1
            return ('{"action":"read_more","page_count":2}' if turns == 1
                    else '{"action":"complete","metadata":{}}')
        with patch.object(LLMRuntime, '_invoke_async', invoke), patch(
                'pdf_craft.extractor.toc.analysing.detect_printed_pages', return_value=[reference]):
            await analyse_toc(self.pages, self.root / 'printed.xml', True, self.llm, toc_executor=toc)
            await self.metadata(metadata)
        self.assertEqual((toc.calls, metadata.calls), (1, 2))

    async def test_role_overrides_do_not_use_truthiness(self):
        common, special = _ObservedExecutor(), _ObservedExecutor()
        refinement = Refinement(JEV('key'), self.llm, executor=common,
                                llm_executor=special)
        self.assertIs(refinement.resolved_jev_executor(), common)
        self.assertIs(refinement.resolved_llm_executor(), special)
        translator = XMLTranslator(self.llm, self.llm, 'en', None, 5, 100,
                                   executor=common, fill_executor=special)
        self.assertIs(translator._translation_runtime.executor, common)
        self.assertIs(translator._fill_runtime.executor, special)
