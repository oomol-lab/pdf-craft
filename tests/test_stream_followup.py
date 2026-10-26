"""Regression tests for scheduling and shared PCEX stream contracts."""

from pdf_craft import RetryPolicy
# pylint: disable=protected-access
import asyncio
import tempfile
import unittest
from pathlib import Path

from pdf_craft.extractor.chapter.stream import resolve_to_disk
from pdf_craft.transformer.xml_translator.xml_translator.concurrency import run_async_parameters
from tests.test_stream_callbacks import page


class StreamFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_first_result_does_not_idle_capacity(self):
        release = asyncio.Event()
        later_finished = asyncio.Event()
        results = []

        async def inputs():
            for index in range(12):
                yield index

        async def execute(index):
            if index == 0:
                await release.wait()
            if index == 11:
                later_finished.set()
            return index

        async def consume():
            async for value in run_async_parameters(inputs(), execute, 2):
                results.append(value)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(later_finished.wait(), 1)
            self.assertEqual(results, [])
        finally:
            release.set()
            await consumer
        self.assertEqual(results, list(range(12)))

    def test_unresolved_source_is_not_appended_after_later_page(self):
        pages = [page(1, 'First body.', 'Unnumbered note.'), page(2, 'Second body.', '')]
        received = []
        with tempfile.TemporaryDirectory() as directory:
            resolve_to_disk(lambda: iter(pages), Path(directory) / 'content.sqlite',
                            on_body=lambda item: received.extend(
                                int(fragment.get('page_index')) for fragment in item.iter('fragment')))
        self.assertEqual(received, sorted(received))

    async def test_two_stage_ocr_finishes_page_before_later_first_pass(self):
        from unittest.mock import patch
        from typing import Any, cast
        from pdf_craft import ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity
        from pdf_craft.pdf.ocr import OCR, OCREventKind
        from pdf_craft.pdf.vendor_ocr import VendorOCRResponse, VendorOCRRuntime
        from tests.test_vendor_ocr_concurrency import _Handler

        second_started = asyncio.Event()
        order = []
        async def request(runtime, operation, started=None):
            async def invoke():
                if started is not None:
                    started.set()
                order.append((operation.page_index, operation.stage_index))
                if operation.page_index == 2 and operation.stage_index == 1:
                    await asyncio.wait_for(second_started.wait(), 2)
                if operation.page_index == 1 and operation.stage_index == 2:
                    second_started.set()
                return VendorOCRResponse({}, raw_text='')
            return await runtime.executor.run(invoke)

        ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://example.invalid/v1',
                  api_key='unused', model='unused'), cast(Any, _Handler([])))
        with tempfile.TemporaryDirectory() as directory, patch.object(
                VendorOCRRuntime, '_request_deepseek', new=request):
            root = Path(directory)
            events = [event async for event in ocr.recognize_vendor(
                ConcurrentExecutor(FixedCapacity(3)), pdf_path=root / 'a.pdf',
                asset_path=root / 'assets', ocr_path=root / 'ocr', includes_footnotes=True)]
        self.assertTrue(second_started.is_set())
        self.assertEqual(sum(event.kind == OCREventKind.COMPLETE for event in events), 3)

    async def test_public_geometry_resource_access_and_read_callbacks(self):
        from pdf_craft import AsyncPDFCraft
        from tests.test_pcex_translation_layers import _source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = _source(root / 'source')
            craft = AsyncPDFCraft()
            pages = await craft.read_pages(document)
            self.assertEqual((pages[1].width, pages[1].height, pages[1].render_dpi), (100, 100, 300))
            self.assertEqual(pages[1].coordinate_space, 'ocr_pixels')
            data = await craft.read_asset(document, 'a' * 64)
            self.assertEqual(data, b'not-decoded-by-validation')
            with self.assertRaises(ValueError):
                await craft.read_asset(document, '../invalid')
            snapshots, notes = [], []
            def body(element):
                snapshots.append((element.text, list(element.regions)))
                element.regions.clear()
            await craft.render_markdown(document, root / 'book.md', on_body=body, on_footnote=notes.append)
            self.assertEqual(len(snapshots), 1)
            self.assertIn('reference', notes[0].text)
            self.assertTrue(notes[0].regions)
            self.assertIn('source', (root / 'book.md').read_text())
            self.assertEqual(snapshots[0][1][0].bbox, (1, 1, 50, 10))
            pages.clear()
            self.assertIn(1, await craft.read_pages(document))

    async def test_geometry_is_available_during_extraction_callback(self):
        from pdf_craft import AsyncPDFCraft, ExtractionOptions, ExtractionContext
        from pdf_craft.transform import PDFExtractionEngine
        from pdf_craft.pdf.ocr import OCREvent, OCREventKind
        from pdf_craft.pdf import encode
        from pdf_craft.common import save_xml
        context = ExtractionContext()
        class FakeOCR:
            def metadata(self, *_args):
                return None

            is_vendor = False
            last_page_pixel_sizes = {1: (100, 100), 2: (100, 100)}
            def recognize(self, **kwargs):
                folder = kwargs['ocr_path']
                folder.mkdir(parents=True, exist_ok=True)
                for index in (1, 2):
                    yield OCREvent(OCREventKind.START, index, 2)
                    save_xml(encode(page(index)), folder / f'page_{index}.xml')
                    yield OCREvent(OCREventKind.COMPLETE, index, 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
            engine.__dict__['_ocr'] = FakeOCR()
            seen = []
            async def body(element):
                self.assertFalse((root / 'book.pcex').exists())
                index = int(element.find('fragment').get('page_index'))
                seen.append(context.read_pages()[index])
            await AsyncPDFCraft.from_engine(engine).extract_pdf(
                root / 'book.pdf', root / 'book.pcex',
                ExtractionOptions(includes_furniture=False, context=context, on_body=body))
            self.assertEqual([geometry.page_index for geometry in seen], [1, 2])
            with self.assertRaises(RuntimeError):
                await context.read_asset('a' * 64)

    async def test_translated_stream_reaches_preview_before_eof_and_pcex_writer(self):
        from unittest.mock import patch
        from pdf_craft import AsyncPDFCraft, ConcurrentExecutor, FixedCapacity, LLM, XMLTranslator
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, encode
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        from tests.extraction_helpers import make_extraction
        release = asyncio.Event()
        first_received = asyncio.Event()
        async def mappings(_translator, inline_segments, **_kwargs):
            results = []
            for segment in inline_segments:
                if any(part.text == 'Second.' for part in segment):
                    await release.wait()
                target = segment.create_element()
                for node in target.iter():
                    if node.text:
                        node.text = '译:' + node.text
                    if node.tail:
                        node.tail = '译:' + node.tail
                results.append((segment.parent, list(search_text_segments(segment.assign_attributes(target)))))
            return results
        llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
        translator = XMLTranslator(llm, llm, 'zh', None,   5, 500,
                                  executor=ConcurrentExecutor(FixedCapacity(2)), retry_policy=RetryPolicy(fill=max(0, (2) - 1)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / 'source'
            source = make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            save_xml(encode(Chapter(None, -1, [TextFlowItem('body', -1, [
                SourceTextFragment(1, index, (0, index * 20, 100, index * 20 + 10), [text])])
                for index, text in enumerate(('First.', 'Second.'))])), source_root / 'chapters/chapter_head.xml')
            received = []
            async def callback(element):
                self.assertFalse((root / 'book.md').exists())
                received.append(element.text)
                first_received.set()
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                pending = asyncio.create_task(AsyncPDFCraft().render_markdown(
                    source, root / 'book.md', translator=translator, with_anchored=False,
                    window=2, on_body=callback))
                try:
                    await asyncio.wait_for(first_received.wait(), 2)
                    self.assertFalse(pending.done())
                finally:
                    release.set()
                    await pending
                self.assertEqual(received, ['译:First.', '译:Second.'])
                self.assertIn('译:Second.', (root / 'book.md').read_text())
                result = await AsyncPDFCraft().translate_extraction(
                    source, root / 'translated.pcex', translator, with_anchored=False, window=2)
                translations = await AsyncPDFCraft().list_translations(result)
                self.assertEqual(len(translations), 1)

    async def test_render_callback_failure_and_cancellation_preserve_output(self):
        from pdf_craft import AsyncPDFCraft
        from tests.test_pcex_translation_layers import _source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / 'source')
            output = root / 'book.md'
            output.write_text('existing output')
            assets = root / 'assets'
            assets.mkdir()
            (assets / 'existing').write_text('existing resource')
            def fail(_element):
                raise ValueError('consumer failed')
            with self.assertRaisesRegex(ValueError, 'consumer failed'):
                await AsyncPDFCraft().render_markdown(source, output, on_body=fail)
            self.assertEqual(output.read_text(), 'existing output')
            entered = asyncio.Event()
            async def wait(_element):
                entered.set()
                await asyncio.Event().wait()
            pending = asyncio.create_task(AsyncPDFCraft().render_markdown(source, output, on_body=wait))
            await asyncio.wait_for(entered.wait(), 1)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertEqual(output.read_text(), 'existing output')
            self.assertEqual((assets / 'existing').read_text(), 'existing resource')

    async def test_epub_and_pdf_consumers_share_pcex_callbacks(self):
        from pdf_craft import AsyncPDFCraft
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, TextFlowItem, SourceTextFragment, Reference, encode
        from tests.extraction_helpers import make_extraction
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', page_pixel_sizes={1: (100, 100)}, with_toc=True)
            ref = Reference(1, 1, '*', [TextFlowItem('body', -1, [
                SourceTextFragment(1, 1, (0, 60, 90, 80), ['Note.'])])])
            save_xml(encode(Chapter(None, -1, [TextFlowItem('body', -1, [
                SourceTextFragment(1, 0, (0, 0, 90, 30), ['Body.', ref])])])),
                root / 'source/chapters/chapter_head.xml')
            bodies, notes = [], []
            await AsyncPDFCraft().render_epub(source, root / 'book.epub',
                                              on_body=bodies.append, on_footnote=notes.append)
            self.assertTrue((root / 'book.epub').is_file())
            self.assertEqual([node.text for node in bodies], ['Body.[^1]'])
            self.assertEqual([node.text for node in notes], ['Note.'])
            expected = [(node.text, [(region.page.page_index, region.bbox) for region in node.regions]) for node in bodies + notes]
            bodies.clear()
            notes.clear()
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_blank_page(100, 100)
            with (root / 'source.pdf').open('wb') as file:
                writer.write(file)
            await AsyncPDFCraft().patch_pdf_with_extraction(root / 'source.pdf', source,
                root / 'book.pdf', on_body=bodies.append, on_footnote=notes.append)
            self.assertEqual([(node.text, [(region.page.page_index, region.bbox) for region in node.regions]) for node in bodies + notes], expected)

    async def test_no_render_callbacks_skip_public_xml_packaging(self):
        from unittest.mock import patch
        from pdf_craft import AsyncPDFCraft
        from tests.test_pcex_translation_layers import _source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / 'source')
            with patch('pdf_craft.document.content._encode_flow', side_effect=AssertionError('unused')), patch(
                    'pdf_craft.document.content._encode_reference', side_effect=AssertionError('unused')):
                await AsyncPDFCraft().render_markdown(source, root / 'book.md')
            self.assertTrue((root / 'book.md').is_file())

    async def test_standard_translation_preserves_references_and_asset_coverage(self):
        from unittest.mock import patch
        from pdf_craft import AsyncPDFCraft, ConcurrentExecutor, FixedCapacity, LLM, XMLTranslator
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        from tests.test_pcex_translation_layers import _source
        from xml.etree.ElementTree import parse
        async def mappings(_translator, inline_segments, **_kwargs):
            result = []
            for segment in inline_segments:
                element = segment.create_element()
                for node in element.iter():
                    if node.tag != 'mark' and node.text and node.text.strip():
                        node.text = '译:' + node.text
                    if node.tail and node.tail.strip():
                        node.tail = '译:' + node.tail
                element = segment.assign_attributes(element)
                # Model the real fill path's preservation of structural slots,
                # including indentation excluded from translatable text.
                for original, translated in zip(segment.parent.iter(), element.iter()):
                    if original.text and not original.text.strip():
                        translated.text = original.text
                    if original.tail and not original.tail.strip():
                        translated.tail = original.tail
                result.append((segment.parent, list(search_text_segments(element))))
            return result
        llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
        translator = XMLTranslator(llm, llm, 'zh', None,   5, 500,
                                  executor=ConcurrentExecutor(FixedCapacity(2)), retry_policy=RetryPolicy(fill=max(0, (2) - 1)))
        with tempfile.TemporaryDirectory() as directory, patch.object(
                XMLTranslator, '_translate_inline_segments_async', new=mappings):
            root = Path(directory)
            source = _source(root / 'source')
            source_bytes = (root / 'source/chapters/chapter_head.xml').read_bytes()
            result = await AsyncPDFCraft().translate_extraction(source, root / 'translated.pcex', translator,
                                                               translation_id='chinese-a', window=2)
            with result._materialize() as paths:
                chapter = parse(paths.translations / 'chinese-a/chapters/chapter_head.xml').getroot()
                occurrence = chapter.find('./flow/text/fragment/ref')
                mark = chapter.find('./references/ref/mark')
                references = chapter.find('./references')
                title = chapter.find('./flow/text/asset/title')
                assert occurrence is not None and mark is not None and references is not None and title is not None
                self.assertEqual(occurrence.get('id'), '1-1')
                self.assertEqual(mark.text, '1')
                self.assertIn('译:reference', ''.join(references.itertext()))
                self.assertIn('译:asset title', ''.join(title.itertext()))
                coverage = parse(paths.translations / 'chinese-a/coverage.xml').getroot()
                asset = coverage.find('./anchored/asset')
                assert asset is not None
                self.assertEqual(asset.get('state'), 'translated')
                self.assertEqual(len(coverage.findall('./narrative/paragraph')), 2)
            second = await AsyncPDFCraft().translate_extraction(result, root / 'second.pcex', translator,
                                                               translation_id='chinese-b', window=2)
            self.assertEqual([layer.id for layer in await AsyncPDFCraft().list_translations(second)],
                             ['chinese-a', 'chinese-b'])
            self.assertEqual((root / 'source/chapters/chapter_head.xml').read_bytes(), source_bytes)

    async def test_callback_failure_interrupts_a_waiting_content_producer(self):
        from pdf_craft.document.content import ContentItem
        from pdf_craft.renderer.paragraph import paragraph_notifications
        from pdf_craft import PageGeometry
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
        closed = asyncio.Event()
        async def source():
            try:
                yield ContentItem('chapter_head.xml', None, -1, 0,
                    TextFlowItem('body', -1, [SourceTextFragment(1, 0, (0, 0, 10, 10), ['Body.'])]))
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def fail(_element):
            raise ValueError('consumer failed')
        async def consume():
            with tempfile.TemporaryDirectory() as directory:
                async with paragraph_notifications({1: PageGeometry(1, 100, 100, 300)}, Path(directory), {}, on_body=fail) as notify:
                    async for item in notify.iterate(source()):
                        await notify.append(item)
        with self.assertRaisesRegex(ValueError, 'consumer failed'):
            await asyncio.wait_for(consume(), 1)
        self.assertTrue(closed.is_set())

    async def test_markdown_assets_can_share_output_directory(self):
        from pdf_craft import AsyncPDFCraft
        from tests.test_pcex_translation_layers import _source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root / 'source')
            (root / 'unrelated').write_text('keep')
            await AsyncPDFCraft().render_markdown(source, root / 'book.md', assets_path=Path('.'))
            self.assertTrue((root / ('a' * 64 + '.png')).exists())
            self.assertEqual((root / 'unrelated').read_text(), 'keep')

    def test_markdown_resource_publication_rolls_back_if_file_commit_fails(self):
        from unittest.mock import patch
        import os
        from pdf_craft.renderer.output import publish
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / 'staged'
            staged.mkdir()
            for name in ('asset.png', 'book.md'):
                (root / name).write_text('old ' + name)
                (staged / name).write_text('new ' + name)
            replace = os.replace
            def fail(source, target):
                if source.name == 'new' and target.name == 'book.md':
                    raise OSError('commit failed')
                return replace(source, target)
            with patch('pdf_craft.renderer.output.os.replace', side_effect=fail):
                with self.assertRaisesRegex(OSError, 'commit failed'):
                    publish([(staged / name, root / name) for name in ('asset.png', 'book.md')])
            for name in ('asset.png', 'book.md'):
                self.assertEqual((root / name).read_text(), 'old ' + name)
            self.assertFalse(list(root.glob('.pdf-craft-*')))

    async def test_standard_translation_stream_preserves_explicit_toc_transform(self):
        for outcome in ('success', 'failure', 'missing'):
            with self.subTest(outcome=outcome):
                await self._check_standard_stream_toc_transform(outcome)

    async def _check_standard_stream_toc_transform(self, outcome):
        from unittest.mock import patch
        from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM, XMLTranslator
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem
        from pdf_craft.extractor.chapter import encode
        from pdf_craft.transformer.package import ChapterExtractionTransformer
        from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
        from pdf_craft.progress import ProgressEventKind
        from pdf_craft.transformer.xml_translator.segment import search_text_segments
        from tests.extraction_helpers import make_extraction

        async def mappings(_translator, inline_segments, **_kwargs):
            return [(segment.parent, list(search_text_segments(
                segment.assign_attributes(segment.create_element())))) for segment in inline_segments]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / 'source', with_toc=outcome != 'missing')
            save_xml(encode(Chapter(None, -1, [TextFlowItem('body', -1, [
                SourceTextFragment(1, 0, (0, 0, 90, 30), ['Body.']),
            ])])), root / 'source/chapters/chapter_head.xml')
            calls, events = [], []
            failure = ValueError('TOC transformation failed')

            async def translate_toc(element):
                calls.append(element)
                if outcome == 'failure':
                    raise failure
                element.set('translated', 'yes')
                return element

            llm = LLM('unused', 'https://example.invalid', 'unused', 'o200k_base')
            translator = XMLTranslator(llm, llm, 'zh', None,   5, 500,
                                      executor=ConcurrentExecutor(FixedCapacity(2)), retry_policy=RetryPolicy(fill=max(0, (2) - 1)))
            transformer = ChapterExtractionTransformer(
                NarrativeXMLTransformer(translator), toc_transformer=translate_toc)
            output = root / 'target.pcex'
            with patch.object(XMLTranslator, '_translate_inline_segments_async', new=mappings):
                if outcome == 'failure':
                    output.write_bytes(b'existing output')
                    with self.assertRaises(ValueError) as caught:
                        await transformer.transform(source, output, on_translation_event=events.append,
                                                    emit_translation_events=True)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(output.read_bytes(), b'existing output')
                    self.assertFalse(any(event.kind == ProgressEventKind.COMPLETED and event.unit_id is None for event in events))
                else:
                    result = await transformer.transform(source, output)
                    with result._materialize() as paths:
                        if outcome == 'success':
                            self.assertIn('translated="yes"', paths.toc.read_text())
                        else:
                            self.assertFalse(paths.toc.exists())
            self.assertEqual(len(calls), 0 if outcome == 'missing' else 1)
            if outcome != 'missing':
                self.assertNotIn('translated="yes"', (root / 'source/toc.xml').read_text())
