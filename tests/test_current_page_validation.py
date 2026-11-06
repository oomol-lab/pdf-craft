"""The page result is the entire semantic authority (service #314/smoke)."""
# pylint: disable=protected-access
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from typing import Any, cast
from unittest.mock import patch

from pdf_craft import RetryPolicy
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.analysis_types import PageStructureError
from pdf_craft.extractor.chapter.live import LiveExtraction
from pdf_craft.extractor.chapter.page_repair import (
    RepairedPage, _compile_repaired_page, repair_page_with_llm, validate_page,
)
from pdf_craft.extractor.refinement import PageResult
from tests.test_footnote_contracts import response
from tests.test_refinement_boundaries import _page, _text
from pdf_craft.extractor.chapter.primary import PrimaryPages


class ForbiddenEvidence:
    """Provenance may be transported/pickled, but never opened for approval."""
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        raise AssertionError(f'Initial semantic evidence was accessed: {name}')

    def __deepcopy__(self, _memo):
        return self


class CurrentPageValidationTests(IsolatedAsyncioTestCase):
    def test_validator_capability_is_only_the_result(self):
        self.assertEqual(list(inspect.signature(validate_page).parameters), ['page'])
        self.assertEqual(list(inspect.signature(PageAssembly.validate).parameters), ['page'])
        page = PrimaryPages().append(_page(2, _text('Current result.')))
        page.layouts[0] = replace(page.layouts[0], source=replace(
            page.layouts[0].source, origin=ForbiddenEvidence()))
        validate_page(page)
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'streams.sqlite')
            assembly.append(page)
            assembly.finish()
            self.assertEqual(len(assembly._run('SELECT * FROM bodies')), 1)

    def test_initial_semantics_cannot_change_compilation_or_acceptance(self):
        initial = PrimaryPages().append(_page(2, _text('Available evidence.')))
        submitted = response(initial)
        submitted['layouts'][0].pop('continues_to_next')
        expected = _compile_repaired_page(initial, RepairedPage.model_validate(submitted))
        initial.citations = cast(Any, ForbiddenEvidence())
        layout = initial.layouts[0]
        initial.layouts[0] = replace(layout, annotations=ForbiddenEvidence(), source=replace(
            layout.source, origin=ForbiddenEvidence()))
        actual = _compile_repaired_page(initial, RepairedPage.model_validate(submitted))
        self.assertEqual(actual.layouts[0].annotations, expected.layouts[0].annotations)
        validate_page(actual)

    async def test_publication_cue_ownership_does_not_gate_incoming_note(self):
        # Reduced from citation-on: old predecessor named p1/source8,
        # '(转下页) 267', excluded by the p1 model. The new protocol names no
        # neighboring source: it appends this page's first note to the cursor.
        initial = PrimaryPages()
        first = initial.append(_page(1, _text('① 前页脚注。') + _text('(转下页) 267')))
        first.layouts[1] = replace(first.layouts[1], source=replace(first.layouts[1].source, source_order=8))
        data = response(first)
        data['citations'] = [dict(citation_id='local-first', index=1, mark='①', stream_order=0)]
        data['layouts'][0].update(ownership='citation', citation_id='local-first', citation_index=1)
        data['layouts'][1].update(ownership='ignored', paragraph_role=None, paragraph_level=None,
                                  continues_from_previous=False, continues_to_next=False)
        first = _compile_repaired_page(first, RepairedPage.model_validate(data))
        second = initial.append(_page(2, _text('脚注继续。')))
        data = response(second)
        data['citations'] = [dict(citation_id='local-next', index=None, mark=None,
                                 stream_order=0, continues_previous=True)]
        data['layouts'][0].update(ownership='citation', citation_id='local-next', citation_index=None,
                                  continues_from_previous=False, continues_to_next=False)
        second = _compile_repaired_page(second, RepairedPage.model_validate(data))
        for excluded in (True, False):
            with self.subTest(cue_excluded=excluded), TemporaryDirectory() as directory:
                variant = deepcopy(first)
                if not excluded:
                    variant.layouts.pop()  # Neighbor membership is irrelevant.
                assembly = PageAssembly(Path(directory)/'streams.sqlite')
                assembly.append(variant)
                run = assembly._run
                def no_neighbor_reads(sql, parameters=(), run=run):
                    self.assertNotIn('FROM adopted', sql)
                    return run(sql, parameters)
                with patch.object(assembly, '_run', no_neighbor_reads):
                    assembly.validate(second)
                    assembly.append(second)
                    assembly.finish()
                notes = assembly._run('SELECT value FROM refs')
                self.assertEqual(len(notes), 1)
                note = pickle.loads(notes[0][0])
                self.assertEqual([child.page_index for item in note.flow_items for child in item.children], [1, 2])
                self.assertEqual(len(note.flow_items), 2, 'Note continuation is distinct from paragraph continuation')

    def test_missing_body_cursor_preserves_content_once(self):
        page = PrimaryPages().append(_page(8, _text('Available tail.')))
        page.layouts[0].annotations.continues_from_previous = True
        with TemporaryDirectory() as directory:
            events = []
            assembly = PageAssembly(Path(directory)/'streams.sqlite', on_body=events.append)
            assembly.validate(page)
            assembly.append(page)
            assembly.finish()
            assembly.finish()
            self.assertEqual(len(events), 1)
            self.assertIn('Available tail.', ''.join(events[0].itertext()))

    async def test_neighbor_model_can_block_without_delaying_local_acceptance(self):
        accepted = asyncio.Event()
        blocked = asyncio.Event()
        @asynccontextmanager
        async def factory():
            async def process(previous, target, following):
                if target.page_index == 1:
                    blocked.set()
                    await asyncio.Event().wait()
                await blocked.wait()
                async def request(*_args):
                    return json.dumps(response(target))
                result = await repair_page_with_llm(
                    previous_page=previous, target_page=target, next_page=following,
                    previous_pass_probability=None, next_pass_probability=None,
                    request=request, retry_policy=RetryPolicy(analysis=0))
                validate_page(result)
                accepted.set()
                return PageResult(result)
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            live = LiveExtraction(root/'live', root, True, refinement=factory)
            try:
                for index in (1, 2):
                    live.pages.append(_page(index, _text(f'Page {index}.')))
                live.pages.close()
                await asyncio.wait_for(accepted.wait(), 2)
                self.assertEqual(live._consumed, 0, 'Ordered delivery may wait, local validation must not')
            finally:
                await live.cancel()

    def test_page_internal_bad_result_is_still_rejected(self):
        page = PrimaryPages().append(_page(1, _text('One ') + _text('paragraph.')))
        page.layouts[0].annotations.continues_to_next = True
        page.layouts[1].annotations.continues_from_previous = True
        page.layouts[1].annotations.paragraph_role = 'heading'
        with self.assertRaisesRegex(PageStructureError, 'GAP_PARAGRAPH_METADATA'):
            validate_page(page)

    def test_range_checks_use_only_the_completed_results_own_contract(self):
        page = PrimaryPages().append(_page(1, _text('abcd') + _text('efgh')))
        first, second = page.layouts
        page.layouts = [replace(first, source=replace(first.source, source_end=4, source_text='abcdefgh')),
                        replace(second, source=replace(second.source, source_order=first.source_order,
                                                       source_start=4, source_end=8, source_text='abcdefgh'))]
        for layout in page.layouts:
            layout.annotations.continues_from_previous = False
            layout.annotations.continues_to_next = False
        validate_page(page)
        for start in (3, 5):
            bad = deepcopy(page)
            bad.layouts[1] = replace(bad.layouts[1], source=replace(bad.layouts[1].source, source_start=start))
            with self.assertRaisesRegex(PageStructureError, 'SOURCE_RANGE'):
                validate_page(bad)

    def test_batch_restoration_hydrates_references_in_asset_fields(self):
        from pdf_craft.extractor.chapter.chapter import Reference, SourceAsset, SourceTextFragment, TextFlowItem
        from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams
        note = Reference(1, 1, '*', [TextFlowItem('body', -1, [
            SourceTextFragment(1, 1, (0, 50, 100, 80), ['Complete note.'])])])
        asset = SourceAsset(1, 'image', (0, 0, 100, 40), title=['Caption', note])
        restored = restore_streams(analyse_pages([1], [asset], [note]))
        final_asset = restored.paragraphs[0]
        assert isinstance(final_asset, SourceAsset)
        self.assertIs(final_asset.title[-1], restored.citations[0])
        self.assertIn('Complete note.', str(final_asset.title[-1]))

    def test_incoming_protocol_has_no_neighbor_membership_contract(self):
        from pydantic import ValidationError
        from pdf_craft.extractor.chapter.page_repair import RepairedCitation
        operation = dict(citation_id='tail', index=None, mark=None, stream_order=0, continues_previous=True)
        self.assertTrue(RepairedCitation.model_validate(operation).continues_previous)
        with self.assertRaises(ValidationError):
            RepairedCitation.model_validate(dict(operation, predecessor=dict(
                page_index=1, source_order=8, start=0, end=6)))

    async def test_valid_adoption_never_opens_the_initial_fallback_candidate(self):
        @asynccontextmanager
        async def factory():
            async def process(_previous, target, _following):
                return PageResult(target, initial=ForbiddenEvidence())
            yield process
        with TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            live = LiveExtraction(root/'live', root, True, refinement=factory, on_body=events.append)
            try:
                with patch('pdf_craft._policy.recover') as recover:
                    live.pages.append(_page(1, _text('Valid adopted result.')))
                    live.pages.close()
                    await asyncio.wait_for(live.finish(), 2)
                    recover.assert_not_called()
                self.assertEqual(len(events), 1)
            finally:
                await live.cancel()

    def test_note_stream_order_overrides_array_order(self):
        page = PrimaryPages().append(_page(2, _text('First note.') + _text('Second note.')))
        submitted = response(page)
        submitted['citations'] = [dict(citation_id='second', index=2, mark='*', stream_order=1),
                                  dict(citation_id='first', index=1, mark='*', stream_order=0)]
        for layout, identifier, index in zip(submitted['layouts'], ['first', 'second'], [1, 2]):
            layout.update(ownership='citation', citation_id=identifier, citation_index=index,
                          continues_from_previous=False, continues_to_next=False)
        result = _compile_repaired_page(page, RepairedPage.model_validate(submitted))
        self.assertEqual([citation.index for citation in result.citations], [1, 2])
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'streams.sqlite')
            assembly.append(result)
            assembly.finish()
            self.assertEqual([len(pickle.loads(row[0]).flow_items) for row in assembly._run(
                'SELECT value FROM refs ORDER BY number')], [1, 1])

    def test_incoming_after_new_note_is_rejected_locally(self):
        page = PrimaryPages().append(_page(2, _text('Incoming tail.') + _text('New note.')))
        submitted = response(page)
        submitted['citations'] = [dict(citation_id='tail', index=None, mark=None, stream_order=1,
                                       continues_previous=True),
                                  dict(citation_id='new', index=1, mark='*', stream_order=0)]
        for layout, identifier, index in zip(submitted['layouts'], ['tail', 'new'], [None, 1]):
            layout.update(ownership='citation', citation_id=identifier, citation_index=index,
                          continues_from_previous=False, continues_to_next=False)
        with self.assertRaisesRegex(ValueError, 'CITATION_ORDER'):
            _compile_repaired_page(page, RepairedPage.model_validate(submitted))
