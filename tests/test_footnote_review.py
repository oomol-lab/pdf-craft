"""Footnote decisions stay local, complete and independent of OCR role guesses."""
from copy import deepcopy
import unittest
from typing import Any

from pdf_craft.extractor.chapter.footnote_review import (
    footnote_review_requests, supplementary_footnote_requests,
    footnote_pass_scores, review_footnote_page,
)


def source(order, text, owner='paragraph', **fields):
    return dict(document_order=order, source_order=order, source_type='text', raw_text=text,
                analysed_text=text, bbox_normalized=[.1, .1 + order * .1, .9, .2 + order * .1],
                ownership=owner, ocr_stream='body' if owner == 'paragraph' else 'footnote',
                text_scale_estimate=10, paragraph_role='body', continues_from_previous=False,
                citation_id='unresolved' if owner == 'citation' else None,
                citation_index=None, references=[], **fields)


def state(*layouts) -> dict[str, Any]:
    return dict(target_page=dict(page_index=2, layouts=list(layouts), citations=[]),
                previous_page=None, next_page=None)


class FootnoteReviewTests(unittest.IsolatedAsyncioTestCase):
    def test_role_questions_cannot_take_guessed_ownership_as_evidence(self):
        value = state(source(0, 'An explanation.'), source(1, '12', 'citation'))
        before = deepcopy(value)
        packets = list(footnote_review_requests(value))
        role = next(p for _, p in packets if 'furniture_1' in p['questions'])
        for layout in role['state']['local_raw_evidence']['target_page']['layouts']:
            self.assertNotIn('ownership', layout)
            self.assertNotIn('ocr_stream', layout)
            self.assertNotIn('citation_id', layout)
            self.assertIn('raw_text', layout)
        self.assertNotIn('ownership', role['state']['checks']['furniture_1'])
        self.assertTrue(all(len(p['questions']) <= 4 for _, p in packets))
        self.assertEqual(value, before)

    def test_new_mark_is_checked_even_when_initial_definition_is_unindexed(self):
        value = state(source(0, r'\* An explanatory note.', 'citation'))
        value['target_page']['citations'] = [dict(citation_id='unresolved', index=None, mark=None)]
        names = {name for _, p in footnote_review_requests(value) for name in p['questions']}
        self.assertIn('new_note_0', names)
        self.assertNotIn('definition_0', names)
        self.assertLess(footnote_pass_scores({'new_note_0': .95})['new_note_0'], .1)

    def test_section_proof_cannot_clear_furniture_or_other_source_fault(self):
        scores = dict(footnote_role_1=.9, furniture_1=.8, footnote_role_2=.7,
                      section_sequence_1=.95, section_member_1=.92)
        passed = footnote_pass_scores(scores)
        self.assertEqual(passed['footnote_role_1'], .92)
        self.assertAlmostEqual(passed['furniture_1'], .2)
        self.assertAlmostEqual(passed['footnote_role_2'], .3)
        self.assertNotIn('section_sequence_1', passed)

    def test_punctuation_does_not_create_fake_semantic_review(self):
        self.assertEqual(list(footnote_review_requests(state(source(0, '.')))), [])

    def test_overlapping_literal_duplicates_reuse_the_shared_detector(self):
        first, second = source(0, 'A printed explanatory sentence.'), source(1, 'explanatory')
        second['bbox_normalized'] = [.2, .12, .5, .18]
        value = state(first, second)
        packets = list(supplementary_footnote_requests(value, {}))
        self.assertIn('redundancy_1', {name for _, p in packets for name in p['questions']})
        second['bbox_normalized'] = [.2, .6, .5, .7]
        self.assertNotIn('redundancy_1', {name for _, p in supplementary_footnote_requests(value, {})
                                        for name in p['questions']})

    async def test_missing_base_or_supplement_is_never_a_partial_pass(self):
        value = state(source(0, 'A sentence.'), source(1, 'sentence'))
        value['target_page']['layouts'][1]['bbox_normalized'] = [.2, .12, .5, .18]
        async def check_missing_call(missing_call):
            calls = []
            async def evaluate(requests):
                rows = [(i, {name: .99 if name.startswith(('gap_', 'redundancy_')) else .01
                             for name in p['questions']}) for i, p in requests]
                calls.append(rows)
                return rows[:-1] if len(calls) == missing_call else rows
            self.assertIsNone(await review_footnote_page(value, evaluate))
            self.assertEqual(len(calls), missing_call)
        for missing_call in (1, 2):
            await check_missing_call(missing_call)

    async def test_complete_positive_furniture_fact_routes_without_other_page_scores(self):
        value = state(source(0, 'A sentence.'), source(1, '28', 'citation'))
        before = deepcopy(value)
        async def evaluate(requests):
            return [(i, {name: .96 if name == 'furniture_1' or name.startswith(('gap_', 'note_gap_'))
                         else .01 for name in p['questions']}) for i, p in requests]
        probability = await review_footnote_page(value, evaluate)
        assert probability is not None
        self.assertAlmostEqual(probability, .04)
        self.assertEqual(value, before)

    async def test_empty_page_has_no_verdict_or_vendor_requests(self):
        observed = []
        async def evaluate(requests):
            observed.extend(requests)
            return []
        self.assertIsNone(await review_footnote_page(state(), evaluate))
        self.assertEqual(observed, [])

    def test_unindexed_citation_checks_raw_section_without_heading_tag(self):
        heading = source(0, 'Commentary')
        target = source(1, 'A continuation of the main commentary.', 'citation')
        target['citation_id'] = 'unresolved-2-0'
        value = state(heading, target)
        before = deepcopy(value)
        packets = list(supplementary_footnote_requests(value, {}))
        packet = next(p for _, p in packets if 'main_section_sequence_1' in p['questions'])
        self.assertIn('main_section_member_1', packet['questions'])
        candidates = packet['state']['checks']['main_section_sequence_1']['preceding_or_current_heading_candidates']
        self.assertEqual([c['source_order'] for c in candidates], [0])
        self.assertEqual(value, before)
        self.assertTrue(all(len(p['questions']) <= 4 for _, p in packets))

    def test_main_section_requires_both_facts_and_only_affects_target(self):
        for sequence, member, expected in ((.95, .91, .09), (.95, .1, .8), (.1, .95, .8)):
            scores = dict(main_reading_1=.2, main_reading_2=.1, furniture_1=.7,
                          main_section_sequence_1=sequence, main_section_member_1=member)
            passed = footnote_pass_scores(scores)
            self.assertAlmostEqual(passed['main_reading_1'], expected)
            self.assertAlmostEqual(passed['main_reading_2'], .9)
            self.assertAlmostEqual(passed['furniture_1'], .3)
            self.assertNotIn('main_section_sequence_1', passed)

    async def test_missing_main_section_fact_cannot_publish_partial_verdict(self):
        target = source(1, 'Main commentary continuation.', 'citation')
        target['citation_id'] = 'unresolved-2-0'
        value = state(source(0, 'Commentary'), target)
        calls = []
        async def evaluate(requests):
            rows = [(i, {name: .95 if name.startswith(('gap_', 'note_gap_', 'main_section_')) else .01
                         for name in p['questions'] if name != 'main_section_member_1'})
                    for i, p in requests]
            calls.extend(rows)
            return rows
        with self.assertRaisesRegex(ValueError, 'named scores'):
            await review_footnote_page(value, evaluate)
        self.assertTrue(any('main_section_sequence_1' in scores for _, scores in calls))

    def test_marked_and_established_notes_do_not_request_main_section_proof(self):
        for text, citation_id in (('* A marked note.', 'unresolved-2-0'),
                                  ('An established note continuation.', 'citation-2-0')):
            target = source(1, text, 'citation')
            target['citation_id'] = citation_id
            names = {name for _, p in supplementary_footnote_requests(state(source(0, 'Commentary'), target), {})
                     for name in p['questions']}
            self.assertFalse(any(name.startswith('main_section_') for name in names))

    def test_section_candidates_are_bounded_and_never_from_next_page(self):
        layouts = [source(i, f'Short candidate {i}') for i in range(5)]
        target = source(5, 'Main passage continuation.', 'citation')
        target['citation_id'] = 'unresolved-2-0'
        value = state(*layouts, target)
        value['next_page'] = dict(page_index=3, paragraph_boundary=[source(6, 'Later commentary')],
                                  citation_boundary=[])
        packets = list(supplementary_footnote_requests(value, {}))
        check = next(p['state']['checks']['main_section_sequence_5'] for _, p in packets
                     if 'main_section_sequence_5' in p['questions'])
        self.assertEqual([c['source_order'] for c in check['preceding_or_current_heading_candidates']], [4, 3, 2])

    def test_section_proof_without_prior_raw_candidate_is_not_invented(self):
        target = source(0, 'An unmarked note.', 'citation')
        target['citation_id'] = 'unresolved-2-0'
        names = {name for _, p in supplementary_footnote_requests(state(target), {})
                 for name in p['questions']}
        self.assertFalse(any(name.startswith('main_section_') for name in names))

    def test_reading_position_proof_cannot_clear_another_role_or_source_fault(self):
        scores = dict(furniture_0=.8, furniture_1=.9, footnote_role_0=.95,
                      retained_reading_position_0=.97)
        passed = footnote_pass_scores(scores)
        self.assertAlmostEqual(passed['furniture_0'], .97)
        self.assertAlmostEqual(passed['furniture_1'], .1)
        self.assertAlmostEqual(passed['footnote_role_0'], .05)
        self.assertNotIn('retained_reading_position_0', passed)
        scores['retained_reading_position_0'] = .02
        self.assertAlmostEqual(footnote_pass_scores(scores)['furniture_0'], .2)

    def test_position_proof_only_checks_retained_reading_suspected_as_apparatus(self):
        value = state(source(0, 'A chapter opening.'), source(1, 'A note.', 'citation'),
                      source(2, 'Excluded material.', 'ignored'), source(3, 'An ordinary paragraph.'))
        before = deepcopy(value)
        packets = list(supplementary_footnote_requests(value, dict(furniture_0=.8, furniture_1=.9,
                                                                 furniture_2=.9, furniture_3=.01)))
        names = {n for _, p in packets for n in p['questions'] if n.startswith('retained_reading_position_')}
        self.assertEqual(names, {'retained_reading_position_0'})
        self.assertEqual(value, before)

    async def test_missing_reading_position_proof_does_not_publish_a_verdict(self):
        async def evaluate(requests):
            rows = []
            for i, packet in requests:
                if any(n.startswith('retained_reading_position_') for n in packet['questions']):
                    continue
                rows.append((i, {n: .8 if n.startswith('furniture_') else .01
                                 for n in packet['questions']}))
            return rows
        self.assertIsNone(await review_footnote_page(state(source(0, 'A chapter opening.')), evaluate))
