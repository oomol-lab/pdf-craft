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
        for missing_call in (1, 2):
            calls = []
            async def evaluate(requests, calls=calls, missing_call=missing_call):
                rows = [(i, {name: .99 if name.startswith(('gap_', 'redundancy_')) else .01
                             for name in p['questions']}) for i, p in requests]
                calls.append(rows)
                return rows[:-1] if len(calls) == missing_call else rows
            self.assertIsNone(await review_footnote_page(value, evaluate))
            self.assertEqual(len(calls), missing_call)

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
