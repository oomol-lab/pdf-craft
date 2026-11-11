"""Feedback explains executable repairs without changing the acceptance rules."""
# pylint: disable=protected-access
from copy import deepcopy
import json
from unittest import IsolatedAsyncioTestCase, TestCase

from pdf_craft import RetryPolicy
from pdf_craft.extractor.chapter.analysis_content import layout_reference_text
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm
from pdf_craft.extractor.chapter.source_ranges import SourceBoundary, _boundary
from tests.test_asset_editing import scene, candidate, compile_page, roundtrip, assets
from tests.test_source_range_repair import page, box, proposal, compiled


class OperationFeedbackTests(TestCase):
    def test_existing_image_flag_feedback_preserves_the_asset_operation(self):
        source = scene(('image', (0, 0, 100, 30), ''))
        data = candidate(source)
        data['layouts'][0]['preserve_as_image'] = True
        with self.assertRaises(ValueError) as caught:
            compile_page(source, data)
        feedback = str(caught.exception)
        for value in ('[GRAPHIC_SOURCE]', 'source_type=image', 'preserve_as_image=false',
                      'ALREADY an asset'):
            self.assertIn(value, feedback)
        data['layouts'][0]['preserve_as_image'] = False
        self.assertEqual(len(assets(roundtrip(compile_page(source, data)))), 1)

    def test_boundary_reports_the_failed_edge_and_nonadjacent_source_quotes(self):
        text = 'First note. Intervening words. 8 Second note.'
        cut = SourceBoundary(left='First note.', right=' 8 Second note.')
        with self.assertRaises(ValueError) as caught:
            _boundary(text, cut, layout_id='layout-example', edge='end')
        feedback = str(caught.exception)
        self.assertIn('layouts[layout-example].source_range.end', feedback)
        self.assertIn('0 matching positions', feedback)
        self.assertIn('Intervening words.', feedback)
        # Diagnostic suggestions are not an alternative, fuzzy matching route.
        correct = SourceBoundary(left='Intervening words.', right=' 8 Second note.')
        self.assertEqual(_boundary(text, correct), text.index('8 Second'))
        repeated = SourceBoundary(left='x', right='y')
        with self.assertRaisesRegex(ValueError, '2 matching positions'):
            _boundary('xy xy', repeated, layout_id='repeat', edge='start')

    def test_backwards_cut_reports_quote_order_and_the_repeated_source_occurrence(self):
        text = 'Main prose resumes here. Note tail ends here.'
        cut = SourceBoundary(left='ends here.', right='Main prose')
        with self.assertRaises(ValueError) as caught:
            _boundary(text, cut, layout_id='mixed', edge='start', entry=3)
        feedback = str(caught.exception)
        for value in ('layouts[3]', f'left quote ends at offsets [{len(text)}]',
                      'right quote begins at offsets [0]', 'backwards/wrapping cut',
                      'Rebuild ALL ranges', '"source_end"', '"source_start"', repr(text)):
            self.assertIn(value, feedback)
        # The better diagnostic does not reinterpret a backwards cut as a match.
        self.assertEqual(cut.left, 'ends here.')

    def test_compiler_identifies_the_actual_repeated_range_entry(self):
        source = page(box('Body. Middle. Ending.'))
        data = proposal(source)
        first = data['layouts'][0]
        cut = dict(left='Body.', right=' Middle.')
        first['source_range'] = dict(start='source_start', end=cut)
        second = dict(first, ownership='ignored', paragraph_role=None, paragraph_level=None,
                      continues_from_previous=False, continues_to_next=False,
                      source_range=dict(start=cut, end=dict(left='Ending.', right='Body.')))
        data['layouts'].append(second)
        with self.assertRaises(ValueError) as caught:
            compiled(source, data)
        feedback = str(caught.exception)
        for value in ('layouts[1]', 'zero-based index', 'source_range.end',
                      'Failing submitted entry:', '"ownership": "ignored"',
                      'Repair source_range.end on THIS entry'):
            self.assertIn(value, feedback)

    def test_unassigned_duplicate_can_be_explicitly_ignored_not_forced_into_a_note(self):
        source = page(box('Body. 8 Note text.'), box('8 Note text.'))
        data = proposal(source)
        mixed, note = data['layouts']
        cut = dict(left='Body.', right=' 8 Note text.')
        mixed.update(source_range=dict(start='source_start', end=cut),
                     continues_from_previous=False, continues_to_next=False)
        note.update(ownership='citation', citation_id='note', citation_index=1,
                    continues_from_previous=False, continues_to_next=False,
                    paragraph_role='body', paragraph_level=-1)
        data['citations'] = [dict(citation_id='note', index=1, mark='8', stream_order=0)]
        with self.assertRaises(ValueError) as caught:
            compiled(source, data)
        feedback = str(caught.exception)
        self.assertIn(mixed['layout_id'], feedback)
        self.assertIn('Unassigned range [6, 18)', feedback)
        self.assertIn('8 Note text.', feedback)
        self.assertIn('ownership=ignored', feedback)
        ignored = dict(mixed, ownership='ignored', paragraph_role=None,
                       paragraph_level=None, citation_id=None, citation_index=None,
                       source_range=dict(start=cut, end='source_end'))
        data['layouts'].insert(1, ignored)
        result = roundtrip(compiled(source, data))
        self.assertEqual(len(result.definitions), 1)
        self.assertEqual(result.definitions[0].mark, '8')
        self.assertEqual(str(result).count('Note text.'), 1)

    def test_overlap_diagnostic_does_not_accept_duplicate_ownership(self):
        source = page(box('Unique text.'))
        data = proposal(source)
        data['layouts'].append(deepcopy(data['layouts'][0]))
        with self.assertRaises(ValueError) as caught:
            compiled(source, data)
        self.assertIn('Multiply assigned range [0, 12)', str(caught.exception))
        self.assertIn('Unique text.', str(caught.exception))

    def test_superscript_conflict_reports_actual_span_and_target_definition(self):
        source = page(box(r'Year \(2005^{8}\).'), box('9 Another note.'))
        data = proposal(source)
        data['citations'] = [dict(citation_id='note', index=1, mark='9', stream_order=0)]
        data['layouts'][-1].update(ownership='citation', citation_id='note', citation_index=1,
                                  paragraph_role='body', paragraph_level=-1)
        data['references'] = [dict(layout_id=data['layouts'][0]['layout_id'], citation_index=1,
                                  anchor=dict(before='2005', mark='^{8}', after='.'),
                                  extract_from_expression=True)]
        with self.assertRaises(ValueError) as caught:
            compiled(source, data)
        feedback = str(caught.exception)
        for value in ('[REF_EXPRESSION]', "Selected source text='^{8}'",
                      "printed mark='9'", "Expected selection='^{9}'", 'citation_index=1'):
            self.assertIn(value, feedback)
        # A semantic mistake is not automatically reclassified by the program.
        self.assertEqual(data['citations'][0]['mark'], '9')
        self.assertEqual(layout_reference_text(source.layouts[0]), 'Year 2005^{8}.')

    def test_note_member_feedback_distinguishes_local_index_from_printed_mark(self):
        source = page(box('Body.'), box('8 A note.'))
        data = proposal(source)
        data['citations'] = [dict(citation_id='note', index=1, mark='8', stream_order=0)]
        member = data['layouts'][-1]
        member.update(ownership='citation', citation_id='note', citation_index=None,
                      paragraph_role='body', paragraph_level=-1)
        with self.assertRaises(ValueError) as caught:
            compiled(source, data)
        feedback = str(caught.exception)
        for value in ('[LAYOUT_CITATION]', 'citation_index=None', 'index=1', "printed mark='8'"):
            self.assertIn(value, feedback)
        member['citation_index'] = 1
        result = compiled(source, data)
        self.assertEqual(result.citations[0].mark, '8')


class OperationLoopTests(IsolatedAsyncioTestCase):
    async def test_caption_repair_changes_destination_instead_of_forcing_body_flags(self):
        source = scene(('image', (0, 0, 100, 20), ''),
                       ('text', (0, 25, 100, 40), 'Note: explanation.'),
                       ('text', (0, 45, 100, 60), 'continues the body.'))
        initial = candidate(source)
        initial['layouts'][2]['continues_from_previous'] = True
        initial['layouts'][1]['continues_to_next'] = False
        before = deepcopy(source)
        attempts = []

        async def request(messages, index, _maximum):
            attempts.append(index)
            data = deepcopy(initial)
            if index:
                self.assertEqual(len(messages), 3)
                self.assertTrue(all(message.role.name != 'ASSISTANT' for message in messages))
                feedback = messages[-1].message
                for value in ('[GAP_MISMATCH]', 'Note: explanation.', 'continues the body.',
                              'asset_attachment', 'independent_stream', 'First reconsider their destinations'):
                    self.assertIn(value, feedback)
                data['layouts'][1]['asset_attachment'] = dict(
                    asset_id=data['layouts'][0]['layout_id'], slot='caption')
            return json.dumps(data)

        repaired = await repair_page_with_llm(
            previous_page=None, target_page=source, next_page=None,
            previous_pass_probability=None, next_pass_probability=None,
            request=request, retry_policy=RetryPolicy(analysis=1))
        self.assertEqual(attempts, [0, 1])
        self.assertEqual(source, before)
        self.assertTrue(repaired.layouts[2].continues_from_previous)
        self.assertEqual(assets(roundtrip(repaired))[0].caption, ['Note: explanation.'])
        # Still reject the unchanged bad candidate; no auto-attachment heuristic.
        with self.assertRaisesRegex(ValueError, 'GAP_MISMATCH'):
            compile_page(source, initial)
