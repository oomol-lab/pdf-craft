"""Progress is an observer; business publication and cancellation remain authoritative."""
# pylint: disable=protected-access
import asyncio
import logging
from functools import partial
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from xml.etree.ElementTree import tostring
from zipfile import ZipFile

from pypdf import PdfReader, PdfWriter

from pdf_craft import AsyncPDFCraft, ConcurrentExecutor, FixedCapacity, ExtractionOptions, ProgressEventKind as Kind, SubmitKind, UnitKind
from pdf_craft._progress import DiskUnits, OperationProgress
from pdf_craft.common import save_xml
from pdf_craft.expression import ExpressionKind
from pdf_craft.extractor.chapter.chapter import (
    Chapter, InlineExpression, Reference, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.pipeline.pdf.patcher import PDFPatcher
from pdf_craft.progress import OperationKind
from pdf_craft.runtime import IO_DOMAIN
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.pcex_progress import scan
from pdf_craft.transformer.word_count import count_xml_words
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator
from tests.extraction_helpers import make_extraction
from tests.test_stream_callbacks import epub
from tests.test_translation_word_progress import translator
from tests import test_work_unit_progress as extraction_fixtures


def source(root):
    document = make_extraction(root)
    # Adjacent formulas need inline preparation before source words are counted.
    paragraph = TextFlowItem('body', 0, [SourceTextFragment(1, 0, (0, 0, 80, 20), [
        'Compute ', InlineExpression(ExpressionKind.INLINE_DOLLAR, r'0.75\times'), ' ',
        InlineExpression(ExpressionKind.INLINE_DOLLAR, '50=37.5'), ' units.',
    ])])
    # A printed note mark alone is not a translatable narrative task.
    empty = Reference(1, 1, '1', [TextFlowItem('body', 0, [
        SourceTextFragment(1, 1, (0, 70, 80, 90), ['']),
    ])])
    fragment = paragraph.children[0]
    assert isinstance(fragment, SourceTextFragment)
    fragment.content.append(empty)
    save_xml(encode(Chapter(None, -1, [paragraph])), root / 'chapters/chapter_head.xml')
    return document


async def identity_segments(_self, inline_segments, **_kwargs):
    protocol = _kwargs.get('translation_protocol')
    mappings = []
    for segment in inline_segments:
        root = segment.assign_attributes(segment.create_element())
        if protocol is not None and protocol.formulas is not None:
            root = protocol.formulas.materialize(root)
        mappings.append((segment.parent, list(search_text_segments(root, normalize_whitespace=False))))
    return mappings


async def draw_copy(_self, original, target, replacements, **_kwargs):
    # Keep the actual content traversal, replacing only PDF drawing.
    await IO_DOMAIN.run(list, replacements)
    Path(target).write_bytes(Path(original).read_bytes())


def operation_completed(events):
    return any(event.kind == Kind.COMPLETED and event.unit_id is None for event in events)


class ExtractionIsolationTests(TestCase):
    def test_reporting_initialization_and_storage_failures_are_auxiliary(self):
        points = ['pdf_craft.extractor.progress.temporary_directory',
                  'pdf_craft.extractor.progress.DiskUnits',
                  'pdf_craft.pdf.page_ref.pdf_pages_count']
        for point in points:
            with self.subTest(point=point), TemporaryDirectory() as directory:
                root = Path(directory)
                events = []
                with patch(point, side_effect=OSError('progress-only initialization')), self.assertLogs(
                        'pdf_craft._progress', level='WARNING') as logs:
                    result = extraction_fixtures.ExtractionUnitTests().craft().extract_pdf('scripted.pdf', root / 'result.pcex',
                        ExtractionOptions(includes_furniture=False, on_extraction_event=events.append))
                result._validate()
                self.assertEqual(len(logs.output), 1)
                self.assertFalse(operation_completed(events))
        for method in ('save', 'load', 'delete'):
            with self.subTest(method=method), TemporaryDirectory() as directory:
                events = []
                with patch.object(DiskUnits, method, side_effect=OSError('progress-only state')), self.assertLogs(
                        'pdf_craft._progress', level='WARNING') as logs:
                    result = extraction_fixtures.ExtractionUnitTests().craft().extract_pdf('scripted.pdf', Path(directory) / 'result.pcex',
                        ExtractionOptions(includes_furniture=False, on_extraction_event=events.append))
                result._validate()
                self.assertEqual(len(logs.output), 1)
                self.assertFalse(operation_completed(events))

    def test_progress_cleanup_cannot_replace_business_result_or_error(self):
        from pdf_craft.runtime import temporary_directory
        from pdf_craft.document.package import PDFCraftExtraction
        @asynccontextmanager
        async def broken_cleanup(prefix):
            async with temporary_directory(prefix) as root:
                yield root
            raise OSError('progress cleanup')
        for business_failure in (False, True):
            with self.subTest(business_failure=business_failure), TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch('pdf_craft.extractor.progress.temporary_directory', broken_cleanup))
                if business_failure:
                    stack.enter_context(patch.object(PDFCraftExtraction, '_export_async', side_effect=ValueError('export')))
                events = []
                with self.assertLogs('pdf_craft._progress', level='WARNING'):
                    def convert(directory, events):
                        return extraction_fixtures.ExtractionUnitTests().craft().extract_pdf(
                            'scripted.pdf', Path(directory) / 'out.pcex',
                            ExtractionOptions(includes_furniture=False, on_extraction_event=events.append))
                    if business_failure:
                        with self.assertRaisesRegex(ValueError, 'export'):
                            convert(directory, events)
                        self.assertFalse(operation_completed(events))
                    else:
                        convert(directory, events)._validate()


class TranslationIsolationTests(IsolatedAsyncioTestCase):
    async def convert(self, kind, root, callback, **kwargs):
        craft = AsyncPDFCraft()
        if kind == 'epub':
            epub(root / 'source.epub')
            await craft.translate_epub(root / 'source.epub', root / 'out.epub', target_language='fr',
                submit=SubmitKind.REPLACE, llm=translator()._translation_llm,
                on_translation_event=callback, executor=ConcurrentExecutor(FixedCapacity(2)), **kwargs)
            with ZipFile(root / 'out.epub') as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn(b'Sentence 0.', archive.read('EPUB/a.xhtml'))
        else:
            document = source(root / 'source')
            if kind == 'pcex':
                result = await craft.translate_extraction(document, root / 'out.pcex', translator(),
                    with_anchored=False, on_translation_event=callback, **kwargs)
                result._validate()
                with ZipFile(root / 'out.pcex') as archive:
                    self.assertIn(b'Compute', archive.read('chapters/chapter_head.xml'))
            else:
                original = root / 'source.pdf'
                writer = PdfWriter()
                writer.add_blank_page(width=100, height=100)
                with original.open('wb') as file:
                    writer.write(file)
                await craft.patch_pdf_with_extraction(original, document, root / 'out.pdf', translator=translator(),
                    on_translation_event=callback, **kwargs)
                self.assertEqual(len(PdfReader(root / 'out.pdf').pages), 1)

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(XMLTranslator, '_translate_inline_segments_async', identity_segments))
        self.stack.enter_context(patch.object(PDFPatcher, 'patch', draw_copy))
        self.addCleanup(self.stack.close)

    async def test_callback_faults_at_every_lifecycle_stage_return_readable_outputs(self):
        for kind in ('pcex', 'epub', 'pdf'):
            for stage in ('operation_start', 'unit_start', 'advance', 'unit_end', 'operation_end'):
                with self.subTest(kind=kind, stage=stage), TemporaryDirectory() as directory:
                    events = []
                    def record(event, *, stage, events):
                        match = { 'operation_start': event.kind == Kind.STARTED and event.unit_id is None,
                                  'unit_start': event.kind == Kind.STARTED and event.unit_id is not None,
                                  'advance': event.kind == Kind.PROGRESS,
                                  'unit_end': event.kind == Kind.COMPLETED and event.unit_id is not None,
                                  'operation_end': event.kind == Kind.COMPLETED and event.unit_id is None}[stage]
                        events.append(event)
                        if match:
                            raise ValueError('broken progress consumer')
                    with self.assertLogs('pdf_craft._progress', level='WARNING') as logs:
                        await self.convert(kind, Path(directory), partial(record, stage=stage, events=events))
                    self.assertEqual(len(logs.output), 1)
                    if stage != 'operation_end':
                        self.assertFalse(operation_completed(events))

    async def test_prescan_and_progress_storage_failure_do_not_block_outputs(self):
        for kind in ('pcex', 'epub', 'pdf'):
            for point in ('scan', 'create', 'save', 'load', 'delete'):
                with self.subTest(kind=kind, point=point), TemporaryDirectory() as directory:
                    events = []
                    target = ('pdf_craft.pipeline.epub.translation.translator.scan_tasks' if kind == 'epub'
                              else 'pdf_craft.transformer.pcex_progress.scan') if point == 'scan' else (
                                  f'pdf_craft._progress.DiskUnits.{"_create" if point == "create" else point}')
                    with patch(target, side_effect=OSError('progress-only state')), self.assertLogs(
                            'pdf_craft._progress', level='WARNING') as logs:
                        await self.convert(kind, Path(directory), events.append)
                    self.assertEqual(len(logs.output), 1)
                    self.assertFalse(operation_completed(events))

    async def test_runtime_source_word_projection_failure_is_auxiliary(self):
        from pdf_craft.transformer.xml_translator.xml_translator.stream_mapper import XMLStreamMapper
        original = XMLStreamMapper._expand_to_resources
        def expand(*args, **kwargs):
            with patch('pdf_craft.transformer.word_count.xml_block_word_counts', side_effect=ValueError('weights')):
                yield from original(*args, **kwargs)
        for kind in ('pcex', 'epub', 'pdf'):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                events = []
                with patch.object(XMLStreamMapper, '_expand_to_resources', expand), self.assertLogs(
                        'pdf_craft._progress', level='WARNING') as logs:
                    await self.convert(kind, Path(directory), events.append)
                self.assertEqual(len(logs.output), 1)
                self.assertFalse(operation_completed(events))

    async def test_business_storage_errors_still_fail_after_progress_is_disabled(self):
        from pdf_craft.document.package import PDFCraftExtraction
        def fail_progress(_event):
            raise ValueError('progress consumer')
        with TemporaryDirectory() as directory, patch.object(
                PDFCraftExtraction, '_export_async', side_effect=OSError('business publication')):
            with self.assertLogs('pdf_craft._progress', level='WARNING'), self.assertRaisesRegex(
                    OSError, 'business publication'):
                await self.convert('pcex', Path(directory), fail_progress)

    async def test_unknown_future_accounting_mismatches_do_not_block_outputs(self):
        original = OperationProgress.start
        async def wrong_estimate(progress, counts, total_words=None):
            counts = dict(counts)
            counts[UnitKind.CONTENT] = counts.get(UnitKind.CONTENT, 0) + 1
            await original(progress, counts, total_words + 7 if total_words is not None else None)
        for kind in ('pcex', 'epub', 'pdf'):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                events = []
                with patch.object(OperationProgress, 'start', wrong_estimate), self.assertLogs(
                        'pdf_craft._progress', level='WARNING') as logs:
                    await self.convert(kind, Path(directory), events.append)
                self.assertEqual(len(logs.output), 1)
                self.assertIn('delta', logs.output[0])
                self.assertTrue(operation_completed(events))
                self.assertEqual(events[0].total_words - events[-1].completed_words, 7)

    async def test_main_task_storage_failure_is_not_mistaken_for_progress_storage(self):
        for kind in ('pcex', 'epub', 'pdf'):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                events = []
                # PDF's disk feed forwards producer failures as RuntimeError;
                # either task may report first. Both must fail the operation.
                error_type = (OSError, RuntimeError) if kind == 'pdf' else OSError
                with patch('pdf_craft.transformer.xml_translator.xml_translator.source_store.TaskStore.save',
                           side_effect=OSError('business task storage')), self.assertRaisesRegex(error_type, 'business task storage'):
                    await self.convert(kind, Path(directory), events.append)
                self.assertFalse(operation_completed(events))

    async def test_content_callback_errors_and_cancellation_still_propagate(self):
        for kind in ('pcex', 'epub', 'pdf'):
            for error in (ValueError('content delivery failed'), asyncio.CancelledError()):
                with self.subTest(kind=kind, error=type(error).__name__), TemporaryDirectory() as directory:
                    events = []
                    def fail(_content, error=error):
                        raise error
                    with self.assertRaises(type(error)):
                        await self.convert(kind, Path(directory), events.append, error_policy=True, **{
                            'on_metadata' if kind == 'pdf' else 'on_body': fail})
                    self.assertFalse(operation_completed(events))

    async def test_progress_callback_cancellation_is_not_swallowed(self):
        for kind in ('pcex', 'epub', 'pdf'):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                events = []
                def cancel(event, *, events):
                    events.append(event)
                    raise asyncio.CancelledError()
                with self.assertRaises(asyncio.CancelledError):
                    await self.convert(kind, Path(directory), partial(cancel, events=events))
                self.assertFalse(operation_completed(events))

    async def test_broken_diagnostic_handler_cannot_prevent_delivery(self):
        def fail(_event):
            raise RuntimeError('progress callback')
        with TemporaryDirectory() as directory, patch.object(logging.getLogger('pdf_craft._progress'), 'warning', side_effect=RuntimeError('logger')):
            await self.convert('pcex', Path(directory), fail)

    async def test_original_source_features_prescan_matches_executed_units_and_words(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = source(root / 'fixture')
            with document._materialize() as paths:
                counts, words = scan(paths, metadata=None, with_anchored=False, with_furniture=False)
            self.assertEqual(counts[UnitKind.CONTENT], 1)  # empty note does not create a unit
            events = []
            await AsyncPDFCraft().translate_extraction(document, root / 'out.pcex', translator(),
                with_anchored=False, on_translation_event=events.append)
            self.assertEqual(events[0].total_words, words)
            self.assertEqual(events[-1].completed_words, words)
            self.assertTrue(operation_completed(events))
            ended = [event for event in events if event.kind == Kind.COMPLETED and event.unit_kind == UnitKind.CONTENT]
            self.assertEqual(len(ended), 1)
            # The formula whitespace difference was genuinely exercised, not removed.
            from pdf_craft.extractor.chapter.chapter import decode
            from pdf_craft.common import read_xml
            chapter = decode(read_xml(root / 'fixture/chapters/chapter_head.xml'))
            raw = encode(chapter)
            self.assertEqual(NarrativeXMLTransformer.source_word_count(chapter), count_xml_words(raw) + 1)
            self.assertEqual(tostring(raw), tostring(encode(chapter)))


class AccountingIsolationTests(IsolatedAsyncioTestCase):
    async def test_progress_only_scratch_directory_failures_do_not_block_public_xml_translation(self):
        from pdf_craft.runtime import temporary_directory
        from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
        from xml.etree.ElementTree import fromstring
        for stage in ('create', 'cleanup'):
            @asynccontextmanager
            async def faulty_directory(prefix, stage=stage):
                if stage == 'create':
                    raise OSError('progress scratch create')
                async with temporary_directory(prefix) as root:
                    yield root
                raise OSError('progress scratch cleanup')
            with self.subTest(stage=stage), patch('pdf_craft._progress.temporary_directory', faulty_directory), patch.object(
                    XMLTranslator, '_translate_inline_segments_async', identity_segments):
                events = []
                task = TranslationTask(fromstring('<body><p>Actual content.</p></body>'), SubmitKind.REPLACE, None)
                with self.assertLogs('pdf_craft._progress', level='WARNING') as logs:
                    element, _ = await translator().translate_element(task, on_translation_event=events.append)
                self.assertEqual(''.join(element.itertext()), 'Actual content.')
                self.assertEqual(len(logs.output), 1)
                if stage == 'create':
                    self.assertFalse(operation_completed(events))

    async def test_cancellation_from_thread_future_and_process_termination_propagate(self):
        from concurrent.futures import CancelledError
        def cancel(_event):
            raise CancelledError()
        progress = OperationProgress(cancel, OperationKind.TRANSLATION)
        with self.assertRaises(asyncio.CancelledError):
            await progress.start({UnitKind.CONTENT: 1})
        def terminate():
            raise SystemExit(2)
        with self.assertRaises(SystemExit):
            progress.attempt_sync(terminate)
        self.assertFalse(progress.disabled)

    async def test_warning_is_bounded_and_reports_actual_without_padding(self):
        events = []
        progress = OperationProgress(events.append, OperationKind.TRANSLATION)
        await progress.start({UnitKind.CONTENT: 3}, 10)
        unit = await progress.unit(UnitKind.CONTENT, words=2)
        await unit.finish()
        with self.assertLogs('pdf_craft._progress', level='WARNING') as logs:
            await progress.finish()
            await progress.finish()
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(events[-1].completed_words, 2)
        self.assertEqual(progress.finished[UnitKind.CONTENT], 1)
        self.assertEqual(len([event for event in events if event.unit_id is None and event.kind == Kind.COMPLETED]), 1)
