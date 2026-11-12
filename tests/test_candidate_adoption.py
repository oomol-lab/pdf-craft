"""Offline cross-page adoption: rejected candidates leave a usable raw route."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy
from pdf_craft.extractor.chapter.analysis_types import PageStructureError, ReferenceLocation
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.chapter import TextFlowItem
from pdf_craft.extractor.chapter.live import LiveExtraction
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm
from pdf_craft.extractor.chapter.primary import PrimaryPages
from pdf_craft.extractor.chapter.stream import chapters_from_disk
from pdf_craft.extractor.refinement import PageResult
from pdf_craft.extractor.recovery import recoverable_structure
from pdf_craft.extractor.toc.types import TocInfo
from pdf_craft.pdf import decode
from tests.test_footnote_contracts import response
from tests.test_refinement_boundaries import _asset, _page, _text


class Usage:
    """Model usage settlement has the production scope's idempotency contract."""
    def __init__(self):
        self.accepted = None

    def settle(self, accepted):
        if self.accepted is None:
            self.accepted = accepted


class CandidateAdoptionTests(IsolatedAsyncioTestCase):
    async def test_local_note_candidates_do_not_reject_each_other_in_either_completion_order(self):
        pages = [decode(fromstring(f'<page index="{i}"><body>{_text(f"Body {i}.")}</body>'
                 f'<footnotes>{_text(note)}</footnotes></page>'))
                 for i, note in enumerate(('① A note starts', 'and continues', 'and ends.'), 1)]
        for completion_order in ((1, 2, 3), (3, 2, 1)):
            with self.subTest(completion_order=completion_order), TemporaryDirectory() as directory:
                root = Path(directory)
                baseline_body, baseline_notes = [], []
                baseline = PageAssembly(root/'baseline.sqlite',
                                        on_body=lambda n: baseline_body.append(tostring(n)),
                                        on_footnote=lambda n: baseline_notes.append(tostring(n)))
                initial = PrimaryPages()
                raw = [deepcopy(initial.append(page)) for page in pages]
                for page in raw:
                    baseline.append(page)
                baseline.finish()
                usages, accepted_locally, decisions = {}, [], []
                ready = {i: asyncio.Event() for i in completion_order}
                completed = []

                @asynccontextmanager
                async def factory():
                    async def process(previous, target, following):
                        proposed = response(target)
                        # Each response passes the real single-page repair path.
                        # The first two disagree with raw neighboring note text.
                        if target.page_index < 3:
                            for layout in proposed['layouts']:
                                layout.pop('continues_to_next', None)
                                if layout['ownership'] == 'citation':
                                    layout.update(paragraph_role='heading', paragraph_level=0)
                        async def request(*_args):
                            return json.dumps(proposed)
                        candidate = await repair_page_with_llm(
                            previous_page=previous, target_page=target, next_page=following,
                            previous_pass_probability=None, next_pass_probability=None,
                            request=request, retry_policy=RetryPolicy(analysis=0))
                        accepted_locally.append(target.page_index)
                        await ready[target.page_index].wait()
                        completed.append(target.page_index)
                        usages[target.page_index] = Usage()
                        return PageResult(candidate, usages[target.page_index], target)
                    yield process

                bodies, notes = [], []
                live = LiveExtraction(root/'live', root, True, refinement=factory,
                                      on_body=lambda n: bodies.append(tostring(n)),
                                      on_footnote=lambda n: notes.append(tostring(n)))
                async def allow(_error, _category, stage, fallback, **_kwargs):
                    decisions.append((stage, fallback))
                try:
                    with patch('pdf_craft._policy.recover', allow):
                        for page in pages:
                            live.pages.append(page)
                        live.pages.close()
                        for index in completion_order:
                            ready[index].set()
                            for _ in range(1000):
                                if index in completed:
                                    break
                                await asyncio.sleep(.001)
                            self.assertIn(index, completed)
                        await asyncio.wait_for(live.finish(), 3)
                    self.assertIsNone(live.analysis_error)
                    self.assertEqual(sorted(accepted_locally), [1, 2, 3])
                    self.assertEqual(completed, list(completion_order))
                    self.assertEqual(decisions, [])
                    self.assertEqual([usages[i].accepted for i in (1, 2, 3)], [True, True, True])
                    self.assertEqual(bodies, baseline_body)
                    self.assertEqual(len(notes), 1)
                    self.assertIn(b'and ends.', notes[0])
                    self.assertEqual(len(list(chapters_from_disk(live.database, TocInfo([], [])))), 1)
                    self.assertEqual(live._consumed, 3)
                finally:
                    await live.cancel()

    async def test_successful_and_failed_probes_restore_every_stream_and_table(self):
        pages = [decode(fromstring(f'<page index="{i}"><body>{_text(f"Body {i}.")}</body>'
                 f'<footnotes>{_text(note)}</footnotes></page>'))
                 for i, note in enumerate(('① Starts', 'and ends.'), 1)]
        initial = PrimaryPages()
        first, second = [deepcopy(initial.append(page)) for page in pages]
        with TemporaryDirectory() as directory:
            events = []
            assembly = PageAssembly(Path(directory)/'work.sqlite', on_body=events.append, on_footnote=events.append)
            assembly.append(first)
            pending = deepcopy((assembly.body.pending, assembly.notes, assembly.active_note))
            tables = {name: assembly._run(f'SELECT * FROM {name}') for name in ('adopted', 'edges', 'bodies', 'refs')}
            assembly.validate(second)
            self.assertFalse(events)
            self.assertEqual(assembly.body.pending, pending[0])
            self.assertEqual(assembly.active_note, pending[2])
            self.assertEqual(set(assembly.notes), set(pending[1]))
            bad = deepcopy(second)
            bad.layouts[0].annotations.references = [ReferenceLocation((0,), 2, 9)]
            with self.assertRaisesRegex(PageStructureError, 'REF_CITATION_UNKNOWN'):
                assembly.validate(bad)
            for name, rows in tables.items():
                self.assertEqual(assembly._run(f'SELECT * FROM {name}'), rows)
            self.assertFalse(events)
            assembly.append(second)
            assembly.finish()
            self.assertEqual(len(events), 3)

    async def test_missing_definition_at_eof_and_bad_incoming_cannot_escape_probe(self):
        page = PrimaryPages().append(_page(1, _text('Text.')))
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'work.sqlite')
            bad = deepcopy(page)
            bad.layouts[0].annotations.references = [ReferenceLocation((0,), 1, 9)]
            with self.assertRaisesRegex(PageStructureError, 'REF_CITATION_UNKNOWN'):
                assembly.validate(bad)
            bad = deepcopy(page)
            bad.layouts[0].annotations.continues_from_previous = True
            assembly.validate(bad)  # No cursor: preserve the available opening.
            assembly.validate(page)
            self.assertFalse(assembly._run('SELECT * FROM adopted'))
            assembly.append(page)
            assembly.finish()
            self.assertEqual(len(list(chapters_from_disk(assembly.database, TocInfo([], [])))), 1)

    async def test_transparent_pages_cannot_freeze_an_earlier_result(self):
        pages = [_page(1, _text('A sentence starts')),
                 *[_page(i, _asset('image')) for i in range(2, 18)],
                 _page(18, _text('and ends.'))]
        initial = PrimaryPages()
        raw = [deepcopy(initial.append(page)) for page in pages]
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'work.sqlite')
            raw[0].layouts[0].annotations.paragraph_role = 'heading'
            for page in raw:
                assembly.validate(page)
                assembly.append(page)
            assembly.finish()
            paragraph = list(chapters_from_disk(assembly.database, TocInfo([], [])))[0].flow_items[0]
            assert isinstance(paragraph, TextFlowItem)
            self.assertEqual(len(paragraph.children), 18)

    async def test_callback_errors_and_storage_errors_are_never_candidate_rejections(self):
        for failure in (ValueError('consumer failed'), OSError('disk failed')):
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                root = Path(directory)
                usage = Usage()
                @asynccontextmanager
                async def factory():
                    async def process(_previous, target, _following):
                        return PageResult(target, usage, deepcopy(target))
                    yield process
                def fail(_value):
                    raise failure
                live = LiveExtraction(root/'live', root, True, refinement=factory, on_body=fail)
                try:
                    with patch('pdf_craft._policy.recover') as recover:
                        live.pages.append(_page(1, _text('Complete paragraph.')))
                        live.pages.close()
                        with self.assertRaises(type(failure)) as caught:
                            if isinstance(failure, OSError):
                                with patch.object(PageAssembly, '_commit_page', side_effect=failure):
                                    await live.task
                            else:
                                await live.finish()
                        self.assertIs(caught.exception, failure)
                        self.assertFalse(recoverable_structure(failure))
                        recover.assert_not_called()
                    if isinstance(failure, OSError):
                        self.assertIs(usage.accepted, False)
                finally:
                    await live.cancel()

    async def test_invalid_raw_fallback_escalates_once_and_delivers_valid_archive(self):
        from zipfile import ZipFile
        from pdf_craft import AsyncPDFCraft, ExtractionOptions
        from pdf_craft.extractor.chapter.chapter import decode as decode_chapter, StandaloneAsset
        from pdf_craft.transform import PDFExtractionEngine
        from tests.test_anchored_page_recovery import RecoveryOCR
        from tests.test_toc_identity import refinement

        original = PrimaryPages.append
        def broken_initial(primary, page, **kwargs):
            result = original(primary, page, **kwargs)
            if result.page_index == 2:
                result.layouts[0].annotations.ownership = 'citation'
                result.layouts[0].annotations.citation_id = 'missing-definition'
            return result
        usages = []
        @asynccontextmanager
        async def factory(_options, _kwargs):
            async def process(_previous, target, _following):
                usage = Usage()
                usages.append(usage)
                return PageResult(target, usage, deepcopy(target))
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
            engine.__dict__['_ocr'] = RecoveryOCR()
            decisions, bodies = [], []
            with patch.object(PrimaryPages, 'append', broken_initial), patch.object(
                    engine, '_stream_refinement', factory), patch.object(
                    RecoveryOCR, 'recover_page_image', autospec=True,
                    side_effect=RecoveryOCR.recover_page_image) as render:
                await AsyncPDFCraft.from_engine(engine).extract_pdf(
                    root/'source.pdf', root/'book.pcex', ExtractionOptions(
                        includes_furniture=False, footnotes=True, refinement=refinement(),
                        on_body=bodies.append), analysing_path=root/'analysis',
                    error_policy=lambda context: decisions.append(context) or True)
            self.assertEqual([context.fallback for context in decisions], ['algorithm_result', 'page_image'])
            self.assertEqual(render.call_count, 1)
            self.assertEqual(render.call_args.args[3], 2)
            self.assertTrue(usages)
            self.assertTrue(any(usage.accepted is True for usage in usages))
            self.assertTrue(any(usage.accepted is False for usage in usages))
            self.assertLessEqual(len(bodies), 1, 'Recovery must not replay content through preview callbacks')
            await AsyncPDFCraft().open_extraction(root/'book.pcex')
            with ZipFile(root/'book.pcex') as archive:
                chapter = decode_chapter(fromstring(archive.read('chapters/chapter_head.xml')))
            self.assertEqual([type(item) for item in chapter.flow_items],
                             [TextFlowItem, StandaloneAsset, TextFlowItem])
            self.assertIn('Page 1.', str(chapter.flow_items[0]))
            self.assertIn('Page 3.', str(chapter.flow_items[2]))

    async def test_footnote_metadata_survives_asset_only_middle_pages(self):
        from pdf_craft.extractor.chapter.chapter import Reference, SourceAsset, SourceTextFragment
        from pdf_craft.extractor.chapter.page_analysis import analyse_pages
        raw = analyse_pages([1, 2, 3, 4], [], [Reference(1, 1, '①', [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 90, 20), ['Note starts']),
            SourceAsset(2, 'image', (0, 0, 90, 20), asset_hash='a' * 64),
            SourceAsset(3, 'table', (0, 0, 90, 20), asset_hash='a' * 64),
            SourceTextFragment(4, 0, (0, 0, 90, 20), ['and ends.']),
        ])])])
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'work.sqlite')
            candidate = deepcopy(raw[0])
            note = next(layout for layout in candidate.layouts if layout.ownership == 'citation')
            note.annotations.paragraph_role = 'heading'
            assembly.validate(candidate)
            raw[0] = candidate
            for page in raw:
                assembly.validate(page)
                assembly.append(page)
            assembly.finish()
            rows = assembly._run('SELECT value FROM refs')
            self.assertEqual(len(rows), 1)
            import pickle
            reference = pickle.loads(rows[0][0])
            self.assertEqual(len(reference.flow_items), 1)
            self.assertEqual([child.page_index for child in reference.flow_items[0].children], [1, 2, 3, 4])
