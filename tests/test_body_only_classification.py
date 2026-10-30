"""Body-only refinement must see and correct both directions of OCR mistakes."""
# pylint: disable=protected-access,unused-argument
import asyncio
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from PIL import Image

from pdf_craft import AsyncPDFCraft, ExtractionOptions, RetryPolicy
from pdf_craft.common import AssetHub, save_xml
from pdf_craft.extractor.chapter.page_analysis import PageStructureError
from pdf_craft.jev import JEVRuntime
from pdf_craft.pdf import decode, encode
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.ocr_config import DeepSeekOCRLocalConfig
from pdf_craft.transform import PDFExtractionEngine
from tests.test_page_extractor_structured import _Block, _Structured
from tests.test_page_repair import _response_from_message
from tests.test_toc_identity import IdentityOCR, refinement


_PAGE = '''<page index="{index}"><body>
<layout ref="text" det="1,20,190,55">The actual body ends here. 3. An inline explanation shares this block.</layout>
<layout ref="text" det="1,140,190,160">1. An explanatory note about the body.</layout>
<layout ref="text" det="1,165,190,190">The bottom paragraph continues</layout>
</body><footnotes><layout ref="text" det="1,60,190,85">Misclassified body must survive.</layout>
<layout ref="text" det="1,195,190,210">2. A real footnote stays excluded.</layout></footnotes></page>'''


class FirstPassEvidenceTests(TestCase):
    def test_asset_origin_survives_analysis_and_restoration(self):
        from pdf_craft.extractor.chapter.primary import PrimaryPages
        from pdf_craft.extractor.chapter.page_analysis import _restore_asset
        raw = decode(fromstring('<page index="1"><body><layout ref="equation" det="1,1,90,20">'
                                'y=2</layout></body><footnotes><layout ref="equation" det="1,80,90,100">'
                                'x=1</layout></footnotes></page>'))
        page = PrimaryPages(False).append(raw)
        self.assertEqual(len(page.layouts), 2)
        for layout, stream, ownership in zip(page.layouts, ('body', 'footnote'), ('paragraph', 'ignored')):
            assert layout.source.origin is not None
            self.assertEqual(layout.source.origin.stream, stream)
            self.assertEqual(layout.ownership, ownership)
            asset = _restore_asset(layout, {})
            self.assertEqual(asset.source, layout.source.origin)
            self.assertEqual(asset.reading_order, layout.source.reading_order)

    def test_first_pass_keeps_footnote_evidence_without_requesting_a_second_pass(self):
        node = PageExtractorNode(DeepSeekOCRLocalConfig())
        image = Image.new('RGB', (200, 220), 'white')
        structured = _Structured([_Block('footnote', (1, 60, 190, 85), text='Misclassified body.')])
        with TemporaryDirectory() as directory:
            layouts = list(node._iter_page_layouts(
                image, structured, AssetHub(Path(directory)), 1, False))
        self.assertEqual(len(layouts), 1)
        self.assertEqual(layouts[0][0].text, 'Misclassified body.')
        self.assertTrue(layouts[0][1])
        image.close()


class BodyOnlyClassificationTests(IsolatedAsyncioTestCase):
    async def test_both_directions_keep_evidence_and_source_identity(self):
        for cached in (False, True):
            for fail_commit in (False, True):
                with self.subTest(cached=cached, fail_commit=fail_commit):
                    await self._extract(cached, fail_commit)

    async def test_cached_note_assets_stay_excluded_on_pass_fallback_and_recovery(self):
        for pass_review, fail_refinement, fail_commit in ((False, False, False), (True, False, False), (False, True, False),
                                                         (True, False, True), (False, True, True)):
            with self.subTest(pass_review=pass_review, fail_refinement=fail_refinement, fail_commit=fail_commit):
                await self._extract(True, fail_commit, pass_review=pass_review,
                                    fail_refinement=fail_refinement, note_assets=True)

    async def test_failed_refinement_preserves_default_exclusion(self):
        await self._extract(True, False, fail_refinement=True)

    async def test_judge_pass_preserves_default_exclusion(self):
        await self._extract(True, False, pass_review=True)

    async def test_restored_body_is_delivered_before_later_ocr_finishes(self):
        await self._extract(False, False, streaming=True)

    async def _extract(self, cached, fail_commit, pass_review=False, streaming=False, fail_refinement=False, note_assets=False):
        packets, prompts, bodies, notes = [], [], [], []
        release = threading.Event()
        blocked, delivered = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        def on_body(value):
            bodies.append(value)
            if 'Misclassified body' in ''.join(value.itertext()):
                delivered.set()

        class OCR(IdentityOCR):
            def recognize(self, **kwargs):
                self_test.assertFalse(kwargs['includes_footnotes'])
                folder = kwargs['ocr_path']
                folder.mkdir(parents=True, exist_ok=True)
                for index in range(1, 7):
                    if streaming and index == 4:
                        loop.call_soon_threadsafe(blocked.set)
                        if not release.wait(5):
                            raise TimeoutError('later OCR was not released')
                    if not cached:
                        yield OCREvent(OCREventKind.START, index, 6)
                    text = _PAGE.format(index=index) if index == 1 else (
                        f'<page index="{index}"><body><layout ref="text" det="1,20,190,55">'
                        'with a genuine continuation.</layout></body><footnotes/></page>')
                    if note_assets and index == 1:
                        text = text.replace('</footnotes>',
                            '<layout ref="equation" det="1,100,190,110">x=17</layout>'
                            '<layout ref="image" det="1,115,190,125">Footnote figure</layout>'
                            '<layout ref="table" det="1,130,190,135">Footnote table</layout></footnotes>')
                    save_xml(encode(decode(fromstring(text))), folder/f'page_{index}.xml')
                    if not cached:
                        yield OCREvent(OCREventKind.COMPLETE, index, 6)

        self_test = self
        async def evaluate(_runtime, index, packet, **kwargs):
            packets.append(packet)
            return .99 if pass_review else .1

        class Runtime:
            async def request(self, messages, **kwargs):
                prompts.append(messages)
                if fail_refinement:
                    raise ValueError('invalid model candidate')
                data = _response_from_message(messages[1])
                if data['page_index'] == 1:
                    by_id = {item['layout_id']: item for item in json.loads(messages[1].message)['target_page']['layouts']}
                    self_test.assertIn('Misclassified body must survive.', [item['text'] for item in by_id.values()])
                    for item in data['layouts']:
                        text = by_id[item['layout_id']]['text']
                        if text.startswith('1. An explanatory'):
                            item.update(ownership='ignored', paragraph_role=None, paragraph_level=None,
                                        continues_from_previous=False)
                        elif text == 'Misclassified body must survive.':
                            item.update(ownership='paragraph', paragraph_role='body', paragraph_level=-1,
                                        continues_from_previous=False)
                return json.dumps(data)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine('unused')
            engine._ocr = cast(Any, OCR())
            commit, calls = engine._commit_extraction, []
            async def commit_once(*args, **kwargs):
                calls.append(1)
                if fail_commit and len(calls) == 1:
                    raise PageStructureError('exercise adopted recovery', 6)
                return await commit(*args, **kwargs)
            engine._commit_extraction = commit_once
            with patch.object(JEVRuntime, 'evaluate', evaluate), patch(
                    'pdf_craft.transform.runtime_for', return_value=Runtime()):
                task = asyncio.create_task(AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=False, refinement=refinement(),
                        on_body=on_body, on_footnote=notes.append),
                    analysing_path=root/'analysis', error_policy=fail_commit or fail_refinement, retry_policy=RetryPolicy(analysis=0)))
                try:
                    if streaming:
                        await asyncio.wait_for(blocked.wait(), 3)
                        await asyncio.wait_for(delivered.wait(), 2)
                        self.assertFalse(task.done())
                finally:
                    release.set()
                    await asyncio.wait_for(task, 10)
            self.assertEqual(len(packets), 6)
            first = next(packet['state']['target_page'] for packet in packets
                         if packet['state']['target_page']['page_index'] == 1)
            candidates = {item['raw_text']: item for item in first['layouts']}
            self.assertEqual(candidates['Misclassified body must survive.']['ocr_stream'], 'footnote')
            self.assertEqual(candidates['Misclassified body must survive.']['ownership'], 'ignored')
            self.assertFalse(first['citations'])
            previous = next(packet['state']['previous_page'] for packet in packets
                            if packet['state']['target_page']['page_index'] == 2)
            if not note_assets:
                self.assertIn('Misclassified body must survive.',
                              [item['raw_text'] for item in previous['excluded_candidates']])
            else:
                self.assertTrue(previous['excluded_candidates'])
                self.assertTrue(all(item['ocr_stream'] == 'footnote' and item['ownership'] == 'ignored'
                                    for item in previous['excluded_candidates']))
            for messages in prompts:
                self.assertIn('不能整块 ignored', messages[0].message)
                self.assertIn('不建立脚注编号', messages[0].message)
                if note_assets and json.loads(messages[1].message)['target_page']['page_index'] == 1:
                    assets = [item for item in json.loads(messages[1].message)['target_page']['layouts']
                              if item['source_type'] in ('formula', 'image', 'table')]
                    self.assertEqual(len(assets), 3)
                    self.assertTrue(all(item['ocr_stream'] == 'footnote' and item['ownership'] == 'ignored'
                                        for item in assets))
            self.assertEqual(len(prompts), 0 if pass_review else 6)
            with ZipFile(root/'book.pcex') as archive:
                chapters = [fromstring(archive.read(name)) for name in archive.namelist()
                            if name.startswith('chapters/') and name.endswith('.xml')]
            text = ''.join(''.join(chapter.itertext()) for chapter in chapters)
            self.assertEqual('Misclassified body must survive.' in text, not (pass_review or fail_refinement))
            self.assertEqual('An explanatory note' in text, pass_review or fail_refinement)
            self.assertNotIn('A real footnote', text)
            if note_assets:
                self.assertNotIn('x=17', text)
                self.assertNotIn('Footnote figure', text)
                self.assertNotIn('Footnote table', text)
                # Recovery may intentionally add a page image for page 6.
                # Only page 1 contains the excluded source assets.
                self.assertFalse(any(asset.attrib.get('page_index') == '1'
                                     for chapter in chapters for asset in chapter.iter('asset')))
                assets = [item for item in first['layouts'] if item['source_type'] in ('formula', 'image', 'table')]
                self.assertEqual(len(assets), 3)
                for item in assets:
                    self.assertEqual(item['ocr_stream'], 'footnote')
                    self.assertEqual(item['ownership'], 'ignored')
            self.assertIn('The actual body ends here. 3. An inline explanation shares this block.', text)
            self.assertIn('The bottom paragraph continues', text)
            self.assertIn('with a genuine continuation.', text)
            continued = next(item for chapter in chapters for item in chapter.iter('text')
                             if 'The bottom paragraph continues' in ''.join(item.itertext()))
            self.assertTrue(any(fragment.attrib.get('page_index') == '2'
                                for fragment in continued.iter('fragment')),
                            'a genuine bottom paragraph still continues onto the next page')
            self.assertFalse(notes)
            self.assertTrue(bodies)
            self.assertFalse(any(list(chapter.iter('ref')) for chapter in chapters))
            raw = (root/'analysis/ocr/page_1.xml').read_text()
            self.assertIn('Misclassified body must survive.', raw)
            self.assertIn('A real footnote', raw)
            if not (pass_review or fail_refinement):
                fragment = next(item for chapter in chapters for item in chapter.iter('fragment')
                                if 'Misclassified body' in ''.join(item.itertext()))
                self.assertEqual(fragment.attrib['source_order'], '3')
