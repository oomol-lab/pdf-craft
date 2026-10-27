"""PAGE boundaries precede costly work, across vendor and local execution."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx
from PIL import Image
from doc_page_extractor.extractor import create_page_extractor_with_adapter
from doc_page_extractor.types import Layout, LayoutKind, OCRPageResult

from pdf_craft import ConcurrentExecutor, DeepSeekOCRLocalConfig, DeepSeekOCRVendorConfig, FixedCapacity
from pdf_craft.common import AssetHub
from pdf_craft.extractor.progress import ExtractionProgress, _active
from pdf_craft.pdf.ocr import OCR
from pdf_craft.pdf.handler import _materialize_pdf_handler
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRRuntime
from pdf_craft.progress import ProgressEventKind, UnitKind
from pdf_craft.runtime import OCR_DOMAIN
from tests.test_vendor_ocr_concurrency import _Handler
from tests.test_async_api import _AsyncDocument, _AsyncHandler


def stages(events, index=1):
    return [e.stage for e in events if e.page_index == index and e.kind == ProgressEventKind.PROGRESS]


class PageOCRStagesTests(IsolatedAsyncioTestCase):
    async def test_async_handler_keeps_pre_rendered_pages_in_preparation_until_ocr(self):
        for budget in (None, 10000):
            with self.subTest(budget=budget), TemporaryDirectory() as folder:
                root, events, requested = Path(folder), [], []
                progress = ExtractionProgress(events.append, root / 'units.sqlite', 2, None)
                token = _active.set(progress)
                async def request(_runtime, value, started=None):
                    if started is not None:
                        started.set()
                    self.assertEqual(stages(events, value.page_index)[-1], 1)
                    requested.append(value.page_index)
                    return VendorOCRResponse({}, raw_text='')
                try:
                    handler = await _materialize_pdf_handler(
                        _AsyncHandler(_AsyncDocument()), root / 'source.pdf', None, 72, root,
                    )
                    for index in (1, 2):
                        self.assertEqual(stages(events, index), [0])
                    ocr = OCR(DeepSeekOCRVendorConfig(
                        base_url='https://example.invalid', api_key='x', model='x',
                    ), handler)
                    with patch.object(VendorOCRRuntime, '_request_deepseek', request):
                        async for event in ocr.recognize_vendor(
                                ConcurrentExecutor(FixedCapacity(1)), pdf_path=root / 'source.pdf',
                                asset_path=root / 'assets', ocr_path=root / 'ocr', includes_footnotes=False,
                                dpi=72, max_tokens=budget, on_preparation_event=progress.ocr):
                            await progress.ocr(event)
                    self.assertEqual(sorted(requested), [1, 2])
                    for index in (1, 2):
                        self.assertEqual(stages(events, index), [0, 0, 0, 1, 4])
                finally:
                    _active.reset(token)

    async def test_vendor_boundaries_precede_requests_mask_and_persistence(self):
        for budget in (None, 10000):
            for footnotes in (False, True):
                with self.subTest(budget=budget, footnotes=footnotes), TemporaryDirectory() as folder:
                    root, events = Path(folder), []
                    progress = ExtractionProgress(events.append, root / 'units.sqlite', 3, {1})
                    await progress.start({UnitKind.PAGE: 1})
                    token = _active.set(progress)
                    ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://example.invalid', api_key='x',
                                                     model='x', retry_times=1, retry_interval_seconds=0), cast(Any, _Handler([])))
                    attempts = {}
                    async def request(_runtime, value, started=None):
                        if started is not None:
                            started.set()
                        self.assertEqual(stages(events)[-1], 1 if value.stage_index == 1 else 3)
                        attempts[value.stage_index] = attempts.get(value.stage_index, 0) + 1
                        if attempts[value.stage_index] == 1:
                            raise httpx.ReadError('retry once')
                        return VendorOCRResponse({}, raw_text='')
                    from pdf_craft.pdf import ocr as module
                    original_mask = module._prepare_second_stage
                    def mask(*args):
                        self.assertEqual(stages(events)[-1], 2)
                        return original_mask(*args)
                    try:
                        with patch.object(VendorOCRRuntime, '_request_deepseek', request), patch.object(
                                module, '_prepare_second_stage', mask):
                            async for event in ocr.recognize_vendor(
                                    ConcurrentExecutor(FixedCapacity(1)), pdf_path=root / 'source.pdf',
                                    asset_path=root / 'assets', ocr_path=root / 'ocr', includes_footnotes=footnotes,
                                    page_indexes={1}, max_tokens=budget, on_preparation_event=progress.ocr):
                                await progress.ocr(event)
                        self.assertEqual(stages(events), [0, 0, 1, 2, 3, 4] if footnotes else [0, 0, 1, 4])
                        self.assertTrue((root / 'ocr/page_1.xml').exists())
                        self.assertFalse(any(e.kind == ProgressEventKind.COMPLETED for e in events))
                        await progress.finish_pages()
                        self.assertEqual(stages(events)[-1], 7)
                        self.assertTrue(all(e.max_stage == 7 for e in events if e.kind == ProgressEventKind.STARTED
                                            and e.unit_kind == UnitKind.PAGE))
                    finally:
                        _active.reset(token)

    async def test_local_passes_preserve_upstream_mask_clipping_and_context(self):
        for double_scan in (False, True):
            with self.subTest(double_scan=double_scan), TemporaryDirectory() as folder:
                root, events, contexts = Path(folder), [], []
                progress = ExtractionProgress(events.append, root / 'units.sqlite', 1, None)
                token = _active.set(progress)
                test = self
                class Adapter:
                    allows_multi_stage = True
                    def download(self, revision=None):
                        pass
                    def load(self):
                        pass
                    def extract_page(self, **kwargs):
                        context = kwargs['context']
                        contexts.append(context)
                        test.assertEqual(stages(events)[-1], 1 if len(contexts) == 1 else 3)
                        context.input_tokens += 5
                        context.output_tokens += 2
                        return OCRPageResult([Layout((0, 0, 12, 16), 'text', kind=LayoutKind.TEXT)], 'test')
                node = PageExtractorNode(DeepSeekOCRLocalConfig())
                node._page_extractor = create_page_extractor_with_adapter(cast(Any, Adapter()))
                from pdf_craft.extractor.progress import page_stage_sync
                def recognize():
                    page_stage_sync(1, 1)
                    return node.image2page(image=Image.new('RGB', (12, 16), 'white'), page_index=1,
                        asset_hub=AssetHub(root / 'assets'), ocr_size='base', includes_footnotes=double_scan,
                        includes_raw_image=False, plot_path=None, max_tokens=100, max_output_tokens=50,
                        device_number=None, aborted=lambda: False)
                try:
                    page = await OCR_DOMAIN.run(recognize)
                    self.assertEqual(stages(events), [1, 2, 3] if double_scan else [1])
                    self.assertEqual(page.input_tokens, 10 if double_scan else 5)
                    self.assertTrue(all(c is contexts[0] for c in contexts))
                    self.assertEqual(len(page.body_layouts), 1)
                    # The second all-page box is fully erased by the original first-pass box.
                    self.assertEqual(page.footnotes_layouts, [])
                finally:
                    _active.reset(token)

    async def test_new_stage_callback_failure_and_cancellation_do_not_complete_pages(self):
        for budget, cancelled in ((None, False), (None, True), (10000, False), (10000, True)):
            with self.subTest(budget=budget, cancelled=cancelled), TemporaryDirectory() as folder:
                root, events = Path(folder), []
                def callback(event):
                    events.append(event)
                    if event.stage == 2:
                        if cancelled:
                            raise asyncio.CancelledError()
                        raise ValueError('consumer stopped')
                progress = ExtractionProgress(callback, root / 'units.sqlite', 3, {1})
                token = _active.set(progress)
                async def request(_runtime, _value, started=None):
                    if started is not None:
                        started.set()
                    return VendorOCRResponse({}, raw_text='')
                from pdf_craft import ErrorCallbackError
                try:
                    ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://example.invalid', api_key='x', model='x'), cast(Any, _Handler([])))
                    with patch.object(VendorOCRRuntime, '_request_deepseek', request):
                        with self.assertRaises(asyncio.CancelledError if cancelled else ErrorCallbackError):
                            async for event in ocr.recognize_vendor(
                                    ConcurrentExecutor(FixedCapacity(1)), pdf_path=root / 'source.pdf',
                                    asset_path=root / 'assets', ocr_path=root / 'ocr', includes_footnotes=True,
                                    error_policy=True, page_indexes={1}, max_tokens=budget,
                                    on_preparation_event=progress.ocr):
                                await progress.ocr(event)
                    self.assertFalse(any(e.kind == ProgressEventKind.COMPLETED for e in events))
                    self.assertFalse((root / 'ocr/page_1.xml').exists())
                finally:
                    _active.reset(token)

    async def test_local_stage_cancellation_stops_without_fallback_or_later_pages(self):
        for cancelled_stage in (2, 3):
            with self.subTest(stage=cancelled_stage), TemporaryDirectory() as folder:
                root, events, native_events, rendered, calls = Path(folder), [], [], [], []
                def callback(event):
                    events.append(event)
                    if event.page_index == 1 and event.stage == cancelled_stage:
                        raise asyncio.CancelledError()
                progress = ExtractionProgress(callback, root / 'units.sqlite', 3, None)
                token = _active.set(progress)
                class Adapter:
                    allows_multi_stage = True
                    def download(self, revision=None):
                        pass
                    def load(self):
                        pass
                    def extract_page(self, **_kwargs):
                        calls.append('recognition')
                        return OCRPageResult([], 'test')
                ocr = OCR(DeepSeekOCRLocalConfig(), cast(Any, _Handler(rendered)))
                ocr._extractor._page_extractor = create_page_extractor_with_adapter(cast(Any, Adapter()))
                from pdf_craft.runtime import callback_bridge
                from pdf_craft.pdf.ocr import OCREventKind
                report = callback_bridge(asyncio.get_running_loop(), progress.ocr)
                def recognize():
                    for event in ocr.recognize(
                            pdf_path=root / 'source.pdf', asset_path=root / 'assets', ocr_path=root / 'ocr',
                            includes_footnotes=True, error_policy=True):
                        native_events.append(event)
                        report(event)
                try:
                    with self.assertRaises(asyncio.CancelledError):
                        await OCR_DOMAIN.run(recognize)
                    self.assertEqual(rendered, [1])
                    self.assertEqual(calls, ['recognition'])
                    self.assertEqual(stages(events)[-1], cancelled_stage)
                    self.assertFalse(any(e.kind in (OCREventKind.COMPLETE, OCREventKind.FAILED)
                                         for e in native_events))
                    self.assertFalse(any(e.kind == ProgressEventKind.COMPLETED for e in events))
                    self.assertEqual(list((root / 'ocr').glob('page_*.xml')), [])
                    self.assertEqual(list((root / 'ocr').glob('page_*.failed')), [])
                    self.assertFalse((root / 'ocr/done').exists())
                finally:
                    _active.reset(token)
